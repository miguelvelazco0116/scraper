from __future__ import annotations

import argparse
import os
from copy import copy
from pathlib import Path

import pandas as pd

from scraper.config import load_categories, load_locations
from scraper.io_utils import atomic_output_path, exclusive_file_lock
from scraper.retailers.chedraui_polanco_api import ChedrauiBlocked, ChedrauiScraper, ChedrauiStoreContextError
from scraper.retailers.farmacias_del_ahorro import (
    FarmaciasDelAhorroBlocked,
    FarmaciasDelAhorroNetworkUnavailable,
    FarmaciasDelAhorroScraper,
)
from scraper.retailers.farmacias_guadalajara import (
    FarmaciasGuadalajaraBlocked,
    FarmaciasGuadalajaraNetworkUnavailable,
    FarmaciasGuadalajaraScraper,
)
from scraper.retailers.farmacias_san_pablo import (
    FarmaciasSanPabloBlocked,
    FarmaciasSanPabloNetworkUnavailable,
    FarmaciasSanPabloScraper,
)
from scraper.retailers.farmacias_similares import (
    FarmaciasSimilaresBlocked,
    FarmaciasSimilaresNetworkUnavailable,
    FarmaciasSimilaresScraper,
)
from scraper.retailers.la_comer import (
    LaComerBlocked,
    LaComerNetworkUnavailable,
    LaComerScraper,
)
from scraper.retailers.ibarra_mayoreo import (
    IbarraMayoreoBlocked,
    IbarraMayoreoNetworkUnavailable,
    IbarraMayoreoScraper,
)
from scraper.retailers.bodega_aurrera import (
    BodegaAurreraBlocked,
    BodegaAurreraNetworkUnavailable,
    BodegaAurreraScraper,
)
from scraper.retailers.soriana import SorianaBlocked, SorianaDeferred, SorianaScraper
from scraper.retailers.walmart import WalmartBlocked, WalmartScraper, WalmartStoreContextError
from scraper.retailers.walmart_persistent import WalmartPersistentScraper
from scraper.retailers.walmart_storage_state import WalmartStorageStateScraper

COLUMNS = [
    "scrape_timestamp", "retailer", "city", "state", "postal_code", "store", "store_id",
    "department", "category", "subcategory", "sub_subcategory", "category_id", "sku", "brand",
    "product", "price_current", "price_regular", "promotion",
    "availability_status", "is_available", "availability_raw", "pickup_available",
    "store_context_verified", "store_context_method", "url", "price_raw",
]

CONSOLIDATED_PATH = Path("output/concentrado_scraper.xlsx")


def deduplicate_catalog(df: pd.DataFrame) -> pd.DataFrame:
    """Deduplicate without collapsing rows that have no SKU and no URL.

    Some retailers (notably San Pablo category cards) expose valid products
    without a stable SKU/URL. Those rows must be deduplicated by content
    instead of treating every (NaN, NaN) pair as the same product.
    """
    if df.empty:
        return df.copy()

    work = df.copy()
    for col in ("sku", "url", "product", "price_current", "price_raw"):
        if col not in work.columns:
            work[col] = None

    sku = work["sku"].fillna("").astype(str).str.strip()
    url = work["url"].fillna("").astype(str).str.strip()
    has_identifier = sku.ne("") | url.ne("")

    with_id = work.loc[has_identifier].drop_duplicates(
        subset=["sku", "url"],
        keep="last",
    )
    without_id = work.loc[~has_identifier].drop_duplicates(
        subset=["product", "price_current", "price_raw"],
        keep="last",
    )

    return pd.concat([with_id, without_id], ignore_index=True)


def evaluate_legacy_output_quality(
    df: pd.DataFrame,
    scraper,
    retailer: str,
) -> tuple[bool, list[str], dict]:
    """Valida una extracción legacy antes de reemplazar el master.

    La última muestra válida se conserva cuando el scraper declara una corrida
    parcial, no alcanza el target publicado o genera filas semánticamente
    incompletas.
    """
    notes: list[str] = []
    meta = (
        getattr(scraper, "run_meta", None)
        or getattr(scraper, "last_meta", None)
        or {}
    )
    meta = dict(meta) if isinstance(meta, dict) else {}

    if df.empty:
        notes.append("sin productos")
        return False, notes, meta

    products = len(df)
    product_complete = int(
        df["product"]
        .fillna("")
        .astype(str)
        .str.strip()
        .ne("")
        .sum()
    )
    if product_complete < products:
        notes.append(
            f"nombre producto {product_complete}/{products}"
        )

    availability = (
        df["availability_status"]
        .fillna("UNKNOWN")
        .astype(str)
        .str.strip()
        .str.upper()
        .replace("", "UNKNOWN")
    )
    price_required = ~availability.eq("UNAVAILABLE")
    required_count = int(price_required.sum())
    current = pd.to_numeric(
        df["price_current"],
        errors="coerce",
    )
    regular = pd.to_numeric(
        df["price_regular"],
        errors="coerce",
    )
    valid_price = int(
        (
            price_required
            & current.notna()
            & current.gt(0)
        ).sum()
    )
    if valid_price < required_count:
        notes.append(
            f"precio requerido {valid_price}/{required_count}"
        )

    price_order_errors = int(
        (
            current.notna()
            & regular.notna()
            & regular.lt(current)
        ).sum()
    )
    if price_order_errors:
        notes.append(
            f"regular<actual {price_order_errors}"
        )

    identifier_required = ~availability.eq("UNAVAILABLE")
    identifier_required_count = int(identifier_required.sum())
    sku_complete = int(
        df.loc[identifier_required, "sku"]
        .fillna("")
        .astype(str)
        .str.strip()
        .ne("")
        .sum()
    )
    url_complete = int(
        df.loc[identifier_required, "url"]
        .fillna("")
        .astype(str)
        .str.strip()
        .ne("")
        .sum()
    )

    if retailer not in {
        "farmacias-san-pablo",
        "ibarra-mayoreo",
    }:
        if sku_complete < identifier_required_count:
            notes.append(
                f"sku requeridos "
                f"{sku_complete}/{identifier_required_count}"
            )
    if retailer != "farmacias-san-pablo":
        if url_complete < identifier_required_count:
            notes.append(
                f"url requeridas "
                f"{url_complete}/{identifier_required_count}"
            )

    raw_status = str(meta.get("status") or "").strip().upper()
    if raw_status in {
        "PARTIAL",
        "EMPTY",
        "BLOCKED",
        "NETWORK_UNAVAILABLE",
        "ERROR",
        "DEFERRED",
    }:
        notes.append(f"scraper status={raw_status}")

    target = meta.get("target_products")
    if target is None:
        target = meta.get("displayed_category_products")
    try:
        target_int = int(target) if target is not None else None
    except (TypeError, ValueError):
        target_int = None

    if target_int is not None and products < target_int:
        notes.append(
            f"cobertura {products}/{target_int}"
        )

    if retailer in {"chedraui", "walmart"}:
        if "store_context_verified" in df.columns:
            verified = df["store_context_verified"].map(
                lambda value: False
                if pd.isna(value)
                else bool(value)
            )
            verified_count = int(verified.sum())
            if verified_count < products:
                notes.append(
                    f"contexto tienda {verified_count}/{products}"
                )

    return not notes, notes, meta


def _update_consolidated_output_unlocked(
    df: pd.DataFrame,
    output_path: Path = CONSOLIDATED_PATH,
) -> Path:
    """Actualiza el consolidado; el caller debe poseer el lock."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    incoming = df.copy()
    for col in COLUMNS:
        if col not in incoming.columns:
            incoming[col] = None
    incoming = incoming[COLUMNS]

    if output_path.exists():
        try:
            existing = pd.read_excel(output_path, sheet_name="Concentrado", dtype={"sku": str, "store_id": str})
        except Exception:
            existing = pd.DataFrame(columns=COLUMNS)
    else:
        existing = pd.DataFrame(columns=COLUMNS)

    for col in COLUMNS:
        if col not in existing.columns:
            existing[col] = None
    existing = existing[COLUMNS]

    if not incoming.empty:
        key_columns = [
            "retailer",
            "category_id",
            "city",
            "store_id",
        ]

        def normalize_key_value(value) -> str:
            if pd.isna(value):
                return ""
            if isinstance(value, float) and value.is_integer():
                return str(int(value))
            return str(value).strip()

        incoming_keys = {
            tuple(normalize_key_value(value) for value in row)
            for row in incoming[key_columns].itertuples(
                index=False,
                name=None,
            )
        }
        existing_keys = [
            tuple(normalize_key_value(value) for value in row)
            for row in existing[key_columns].itertuples(
                index=False,
                name=None,
            )
        ]
        replace_mask = pd.Series(
            [key in incoming_keys for key in existing_keys],
            index=existing.index,
            dtype=bool,
        )
        existing = existing.loc[~replace_mask].copy()

    if existing.empty:
        combined = incoming.copy()
    elif incoming.empty:
        combined = existing.copy()
    else:
        # Evita el FutureWarning de pandas al concatenar columnas all-NA.
        # Se eliminan temporalmente sólo las columnas completamente vacías
        # de cada fragmento y después se restaura el esquema canónico.
        concat_parts = [
            frame.dropna(axis=1, how="all")
            for frame in (existing, incoming)
        ]
        combined = pd.concat(concat_parts, ignore_index=True, sort=False)
        for col in COLUMNS:
            if col not in combined.columns:
                combined[col] = None
        combined = combined[COLUMNS]
    if not combined.empty:
        sku_text = combined["sku"].fillna("").astype(str).str.strip()
        url_text = combined["url"].fillna("").astype(str).str.strip()
        id_mask = sku_text.ne("") | url_text.ne("")

        with_id = combined.loc[id_mask].drop_duplicates(
            subset=["retailer", "category_id", "city", "store_id", "sku", "url"],
            keep="last",
        )
        without_id = combined.loc[~id_mask].drop_duplicates(
            subset=[
                "retailer", "category_id", "city", "store_id",
                "product", "price_current", "price_raw",
            ],
            keep="last",
        )
        combined = pd.concat([with_id, without_id], ignore_index=True)
        combined = combined.sort_values(
            ["retailer", "category_id", "brand", "product"],
            na_position="last",
        ).reset_index(drop=True)

    if combined.empty:
        summary = pd.DataFrame(
            columns=[
                "retailer", "category_id", "city", "store", "store_id",
                "products", "sku_complete", "price_complete", "url_complete",
                "available_products", "unavailable_products", "availability_unknown",
            ]
        )
    else:
        summary = (
            combined.groupby(["retailer", "category_id", "city", "store", "store_id"], dropna=False)
            .agg(
                products=("sku", "size"),
                sku_complete=("sku", lambda s: int(s.notna().sum())),
                price_complete=("price_current", lambda s: int(s.notna().sum())),
                url_complete=("url", lambda s: int(s.notna().sum())),
                available_products=("availability_status", lambda s: int(s.fillna("UNKNOWN").astype(str).str.upper().eq("AVAILABLE").sum())),
                unavailable_products=("availability_status", lambda s: int(s.fillna("UNKNOWN").astype(str).str.upper().eq("UNAVAILABLE").sum())),
                availability_unknown=("availability_status", lambda s: int(s.fillna("UNKNOWN").astype(str).str.upper().eq("UNKNOWN").sum())),
            )
            .reset_index()
        )

    with atomic_output_path(output_path) as temporary_output:
        with pd.ExcelWriter(temporary_output, engine="openpyxl") as writer:
            combined.to_excel(writer, index=False, sheet_name="Concentrado")
            summary.to_excel(writer, index=False, sheet_name="Resumen")

            workbook = writer.book
            for sheet_name in ("Concentrado", "Resumen"):
                ws = workbook[sheet_name]
                ws.freeze_panes = "A2"
                ws.auto_filter.ref = ws.dimensions
                for cell in ws[1]:
                    header_font = copy(cell.font)
                    header_font.bold = True
                    cell.font = header_font
                for col_cells in ws.columns:
                    values = [
                        str(cell.value) if cell.value is not None else ""
                        for cell in col_cells[:200]
                    ]
                    width = min(
                        max(
                            max(
                                (len(value) for value in values),
                                default=0,
                            )
                            + 2,
                            10,
                        ),
                        42,
                    )
                    ws.column_dimensions[
                        col_cells[0].column_letter
                    ].width = width

            for cell in workbook["Concentrado"]["P"]:
                if cell.row > 1:
                    cell.number_format = '$#,##0.00'
            for cell in workbook["Concentrado"]["Q"]:
                if cell.row > 1:
                    cell.number_format = '$#,##0.00'

    return output_path


def update_consolidated_output(
    df: pd.DataFrame,
    output_path: Path = CONSOLIDATED_PATH,
) -> Path:
    """Actualiza el Excel maestro de forma serializada y atómica.

    El lock cubre todo el ciclo read-modify-write para evitar pérdida de
    actualizaciones cuando dos procesos intentan consolidar al mismo tiempo.
    """
    output_path = Path(output_path)
    with exclusive_file_lock(output_path):
        return _update_consolidated_output_unlocked(df, output_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Scraper multi-retailer")
    parser.add_argument(
        "--retailer",
        default="soriana",
        choices=[
            "soriana", "walmart", "chedraui", "farmacias-guadalajara",
            "farmacias-del-ahorro", "farmacias-san-pablo", "farmacias-similares",
            "la-comer", "ibarra-mayoreo", "bodega-aurrera",
        ],
    )
    parser.add_argument("--category", default="cuidado-bucal")
    parser.add_argument("--location", default=None)
    parser.add_argument("--store", default=None, help="Alias de ubicación para una tienda configurada")
    parser.add_argument(
        "--profile-dir",
        default=None,
        help="Perfil persistente de Playwright/Chrome para retailers compatibles.",
    )
    parser.add_argument("--storage-state", default=None, help="Sesión portable de Playwright para Walmart")
    parser.add_argument("--headed", action="store_true", help="Abrir navegador visible")
    parser.add_argument(
        "--browser-channel",
        default=None,
        help="Canal de navegador Playwright, por ejemplo: chrome",
    )
    parser.add_argument("--max-load-more", type=int, default=100)
    args = parser.parse_args()

    category_path = f"config/{args.retailer}/categories.yaml"
    category = next((x for x in load_categories(category_path) if x.id == args.category), None)
    if args.retailer == "walmart":
        default_location = "sc-toreo"
    elif args.retailer == "chedraui":
        default_location = "chedraui-polanco"
    elif args.retailer == "farmacias-guadalajara":
        default_location = "fg-online"
    elif args.retailer == "farmacias-del-ahorro":
        default_location = "fahorro-online"
    elif args.retailer == "farmacias-san-pablo":
        default_location = "san-pablo-online"
    elif args.retailer == "farmacias-similares":
        default_location = "similares-online"
    elif args.retailer == "la-comer":
        default_location = "la-comer-online-287"
    elif args.retailer == "ibarra-mayoreo":
        default_location = "ibarra-online"
    elif args.retailer == "bodega-aurrera":
        default_location = "bodega-aurrera-online"
    else:
        default_location = "cdmx"
    location_id = args.store or args.location or default_location
    location = next((x for x in load_locations() if x.id == location_id), None)
    if category is None:
        raise SystemExit(f"Categoría no encontrada: {args.category}")
    if location is None:
        raise SystemExit(f"Ubicación/tienda no encontrada: {location_id}")

    if args.retailer == "soriana":
        profile_dir = args.profile_dir or ".soriana_profile"
        scraper = SorianaScraper(
            headless=not args.headed,
            max_load_more=args.max_load_more,
            browser_channel=args.browser_channel,
            profile_dir=profile_dir,
        )
        try:
            rows = scraper.scrape_category(category, location)
        except SorianaDeferred as exc:
            print(f"DEFERRED: {exc}")
            return 6
        except SorianaBlocked as exc:
            print(f"BLOCKED: {exc}")
            return 2
    elif args.retailer == "walmart":
        storage_state = args.storage_state or os.getenv("WALMART_STORAGE_STATE_FILE")
        profile_dir = args.profile_dir or os.getenv("WALMART_USER_DATA_DIR")
        if storage_state:
            scraper = WalmartStorageStateScraper(
                storage_state_path=storage_state,
                headless=not args.headed,
                max_pages=args.max_load_more,
            )
        elif profile_dir:
            scraper = WalmartPersistentScraper(
                user_data_dir=profile_dir,
                headless=not args.headed,
                max_pages=args.max_load_more,
            )
        else:
            scraper = WalmartScraper(headless=not args.headed, max_pages=args.max_load_more)
        try:
            rows = scraper.scrape_category(category, location)
        except WalmartBlocked as exc:
            print(f"BLOCKED: {exc}")
            return 2
        except WalmartStoreContextError as exc:
            print(f"STORE_CONTEXT_ERROR: {exc}")
            return 4
    elif args.retailer == "chedraui":
        profile_dir = args.profile_dir or ".chedraui_profile"
        scraper = ChedrauiScraper(
            headless=not args.headed,
            max_pages=args.max_load_more,
            browser_channel=args.browser_channel,
            profile_dir=profile_dir,
        )
        try:
            rows = scraper.scrape_category(category, location)
        except ChedrauiBlocked as exc:
            print(f"BLOCKED: {exc}")
            return 2
        except ChedrauiStoreContextError as exc:
            print(f"STORE_CONTEXT_ERROR: {exc}")
            return 4
    elif args.retailer == "farmacias-guadalajara":
        scraper = FarmaciasGuadalajaraScraper(
            headless=not args.headed,
            max_load_more=args.max_load_more,
        )
        try:
            rows = scraper.scrape_category(category, location)
        except FarmaciasGuadalajaraBlocked as exc:
            print(f"BLOCKED: {exc}")
            return 2
        except FarmaciasGuadalajaraNetworkUnavailable as exc:
            print(f"NETWORK_UNAVAILABLE: {exc}")
            return 5
    elif args.retailer == "farmacias-del-ahorro":
        scraper = FarmaciasDelAhorroScraper(
            headless=not args.headed,
            max_pages=args.max_load_more,
        )
        try:
            rows = scraper.scrape_category(category, location)
        except FarmaciasDelAhorroBlocked as exc:
            print(f"BLOCKED: {exc}")
            return 2
        except FarmaciasDelAhorroNetworkUnavailable as exc:
            print(f"NETWORK_UNAVAILABLE: {exc}")
            return 5
    elif args.retailer == "farmacias-san-pablo":
        scraper = FarmaciasSanPabloScraper(
            headless=not args.headed,
            max_pages=args.max_load_more,
            profile_dir=args.profile_dir,
        )
        try:
            rows = scraper.scrape_category(category, location)
        except FarmaciasSanPabloBlocked as exc:
            print(f"BLOCKED: {exc}")
            return 2
        except FarmaciasSanPabloNetworkUnavailable as exc:
            print(f"NETWORK_UNAVAILABLE: {exc}")
            return 5
    elif args.retailer == "farmacias-similares":
        scraper = FarmaciasSimilaresScraper(
            headless=not args.headed,
            browser_channel=args.browser_channel or "chrome",
            max_pages=args.max_load_more,
            profile_dir=args.profile_dir,
        )
        try:
            rows = scraper.scrape_category(category, location)
        except FarmaciasSimilaresBlocked as exc:
            print(f"BLOCKED: {exc}")
            return 2
        except FarmaciasSimilaresNetworkUnavailable as exc:
            print(f"NETWORK_UNAVAILABLE: {exc}")
            return 5
    elif args.retailer == "la-comer":
        scraper = LaComerScraper(
            headless=not args.headed,
            browser_channel=args.browser_channel or "chrome",
            max_scroll_rounds=args.max_load_more,
            profile_dir=args.profile_dir,
        )
        try:
            rows = scraper.scrape_category(category, location)
        except LaComerBlocked as exc:
            print(f"BLOCKED: {exc}")
            return 2
        except LaComerNetworkUnavailable as exc:
            print(f"NETWORK_UNAVAILABLE: {exc}")
            return 5
    elif args.retailer == "ibarra-mayoreo":
        scraper = IbarraMayoreoScraper(
            headless=not args.headed,
            browser_channel=args.browser_channel or "chrome",
            max_pages=args.max_load_more,
        )
        try:
            rows = scraper.scrape_category(category, location)
        except IbarraMayoreoBlocked as exc:
            print(f"BLOCKED: {exc}")
            return 2
        except IbarraMayoreoNetworkUnavailable as exc:
            print(f"NETWORK_UNAVAILABLE: {exc}")
            return 5
    elif args.retailer == "bodega-aurrera":
        scraper = BodegaAurreraScraper(
            headless=not args.headed,
            browser_channel=args.browser_channel or "chrome",
            max_pages=args.max_load_more,
            profile_dir=args.profile_dir,
        )
        try:
            rows = scraper.scrape_category(category, location)
        except BodegaAurreraBlocked as exc:
            print(f"BLOCKED: {exc}")
            return 2
        except BodegaAurreraNetworkUnavailable as exc:
            print(f"NETWORK_UNAVAILABLE: {exc}")
            return 5
    else:
        raise SystemExit(f"Retailer no implementado: {args.retailer}")

    for row in rows:
        row["department"] = category.department
        row["category"] = category.name
        row["subcategory"] = category.subcategory
        row["sub_subcategory"] = category.sub_subcategory
        row["category_id"] = category.id

    df = pd.DataFrame(rows, columns=COLUMNS)
    if not df.empty:
        df = deduplicate_catalog(df)
        df = df.sort_values(["brand", "product"], na_position="last").reset_index(drop=True)

    quality_pass, quality_notes, quality_meta = (
        evaluate_legacy_output_quality(
            df,
            scraper,
            args.retailer,
        )
    )

    if quality_pass:
        consolidated_path = update_consolidated_output(df)
        print("QUALITY_GATE: PASS")
    else:
        consolidated_path = CONSOLIDATED_PATH
        print(
            "QUALITY_GATE: FAIL | "
            + "; ".join(quality_notes)
        )
        print(
            "Consolidado protegido: se conserva la última muestra válida."
        )

    if args.retailer == "chedraui":
        displayed = getattr(scraper, "run_meta", {}).get("displayed_category_products")
        collected = getattr(scraper, "run_meta", {}).get("collected_products")
        coverage = getattr(scraper, "run_meta", {}).get("displayed_count_coverage")
        if displayed is not None:
            print(
                f"Chedraui catálogo tienda: displayed={displayed}, "
                f"collected={collected}, coverage={coverage}"
            )

    print(f"Productos únicos: {len(df)}")
    print(f"Concentrado: {consolidated_path}")
    if df.empty:
        print("No se encontraron productos. Revisa diagnostics/.")
        return 3
    if not quality_pass:
        return 7
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
