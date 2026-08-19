from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd

from scraper.config import load_categories, load_locations
from scraper.retailers.chedraui_polanco_api import ChedrauiBlocked, ChedrauiScraper, ChedrauiStoreContextError
from scraper.retailers.soriana import SorianaBlocked, SorianaScraper
from scraper.retailers.walmart import WalmartBlocked, WalmartScraper, WalmartStoreContextError
from scraper.retailers.walmart_persistent import WalmartPersistentScraper
from scraper.retailers.walmart_storage_state import WalmartStorageStateScraper

COLUMNS = [
    "scrape_timestamp", "retailer", "city", "state", "postal_code", "store", "store_id",
    "department", "category", "subcategory", "sub_subcategory", "category_id", "sku", "brand",
    "product", "price_current", "price_regular", "promotion", "pickup_available",
    "store_context_verified", "store_context_method", "url", "price_raw",
]

CONSOLIDATED_PATH = Path("output/concentrado_scraper.xlsx")


def update_consolidated_output(df: pd.DataFrame, output_path: Path = CONSOLIDATED_PATH) -> Path:
    """Actualiza un único Excel consolidado con la extracción actual."""
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
        retailer = str(incoming.iloc[0]["retailer"])
        category_id = str(incoming.iloc[0]["category_id"])
        city = incoming.iloc[0]["city"]
        store_id = incoming.iloc[0]["store_id"]

        same_retailer = existing["retailer"].astype(str).eq(retailer)
        same_category = existing["category_id"].astype(str).eq(category_id)
        same_city = existing["city"].fillna("").astype(str).eq("" if pd.isna(city) else str(city))
        same_store = existing["store_id"].fillna("").astype(str).eq("" if pd.isna(store_id) else str(store_id))
        existing = existing.loc[~(same_retailer & same_category & same_city & same_store)].copy()

    combined = pd.concat([existing, incoming], ignore_index=True)
    if not combined.empty:
        combined["sku"] = combined["sku"].astype(str)
        combined = combined.drop_duplicates(
            subset=["retailer", "category_id", "city", "store_id", "sku", "url"],
            keep="last",
        )
        combined = combined.sort_values(
            ["retailer", "category_id", "brand", "product"],
            na_position="last",
        ).reset_index(drop=True)

    if combined.empty:
        summary = pd.DataFrame(
            columns=["retailer", "category_id", "city", "store", "store_id", "products", "sku_complete", "price_complete", "url_complete"]
        )
    else:
        summary = (
            combined.groupby(["retailer", "category_id", "city", "store", "store_id"], dropna=False)
            .agg(
                products=("sku", "size"),
                sku_complete=("sku", lambda s: int(s.notna().sum())),
                price_complete=("price_current", lambda s: int(s.notna().sum())),
                url_complete=("url", lambda s: int(s.notna().sum())),
            )
            .reset_index()
        )

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        combined.to_excel(writer, index=False, sheet_name="Concentrado")
        summary.to_excel(writer, index=False, sheet_name="Resumen")

        workbook = writer.book
        for sheet_name in ("Concentrado", "Resumen"):
            ws = workbook[sheet_name]
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for cell in ws[1]:
                cell.font = cell.font.copy(bold=True)
            for col_cells in ws.columns:
                values = [str(c.value) if c.value is not None else "" for c in col_cells[:200]]
                width = min(max(max((len(v) for v in values), default=0) + 2, 10), 42)
                ws.column_dimensions[col_cells[0].column_letter].width = width

        for cell in workbook["Concentrado"]["P"]:
            if cell.row > 1:
                cell.number_format = '$#,##0.00'
        for cell in workbook["Concentrado"]["Q"]:
            if cell.row > 1:
                cell.number_format = '$#,##0.00'

    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Scraper multi-retailer")
    parser.add_argument("--retailer", default="soriana", choices=["soriana", "walmart", "chedraui"])
    parser.add_argument("--category", default="cuidado-bucal")
    parser.add_argument("--location", default=None)
    parser.add_argument("--store", default=None, help="Alias de ubicación para una tienda configurada")
    parser.add_argument("--profile-dir", default=None, help="Perfil persistente de Playwright para Walmart")
    parser.add_argument("--storage-state", default=None, help="Sesión portable de Playwright para Walmart")
    parser.add_argument("--headed", action="store_true", help="Abrir navegador visible")
    parser.add_argument("--max-load-more", type=int, default=100)
    args = parser.parse_args()

    category_path = f"config/{args.retailer}/categories.yaml"
    category = next((x for x in load_categories(category_path) if x.id == args.category), None)
    if args.retailer == "walmart":
        default_location = "sc-toreo"
    elif args.retailer == "chedraui":
        default_location = "chedraui-polanco"
    else:
        default_location = "cdmx"
    location_id = args.store or args.location or default_location
    location = next((x for x in load_locations() if x.id == location_id), None)
    if category is None:
        raise SystemExit(f"Categoría no encontrada: {args.category}")
    if location is None:
        raise SystemExit(f"Ubicación/tienda no encontrada: {location_id}")

    if args.retailer == "soriana":
        scraper = SorianaScraper(headless=not args.headed, max_load_more=args.max_load_more)
        try:
            rows = scraper.scrape_category(category, location)
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
        scraper = ChedrauiScraper(headless=not args.headed, max_pages=args.max_load_more)
        try:
            rows = scraper.scrape_category(category, location)
        except ChedrauiBlocked as exc:
            print(f"BLOCKED: {exc}")
            return 2
        except ChedrauiStoreContextError as exc:
            print(f"STORE_CONTEXT_ERROR: {exc}")
            return 4
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
        df = df.drop_duplicates(subset=["sku", "url"], keep="last")
        df = df.sort_values(["brand", "product"], na_position="last").reset_index(drop=True)

    consolidated_path = update_consolidated_output(df)

    print(f"Productos únicos: {len(df)}")
    print(f"Concentrado: {consolidated_path}")
    if df.empty:
        print("No se encontraron productos. Revisa diagnostics/.")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
