from __future__ import annotations

import argparse
import sys
from copy import copy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from main import COLUMNS, CONSOLIDATED_PATH, update_consolidated_output
from scraper.config import load_categories, load_locations
from scraper.io_utils import atomic_output_path
from scraper.retailers.farmacias_san_pablo import (
    FarmaciasSanPabloBlocked,
    FarmaciasSanPabloNetworkUnavailable,
    FarmaciasSanPabloScraper,
)


OUTPUT_PATH = ROOT / "output" / "farmacias_san_pablo_test.xlsx"


def _write_test_output(
    rows: list[dict],
    summaries: list[dict],
    output_path: Path = OUTPUT_PATH,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    df = pd.DataFrame(rows, columns=COLUMNS)
    summary = pd.DataFrame(summaries)

    with atomic_output_path(output_path) as temporary_output:
        with pd.ExcelWriter(temporary_output, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Concentrado")
            summary.to_excel(writer, index=False, sheet_name="Resumen")

            workbook = writer.book
            for sheet_name in ("Concentrado", "Resumen"):
                ws = workbook[sheet_name]
                ws.freeze_panes = "A2"
                ws.auto_filter.ref = ws.dimensions
                for cell in ws[1]:
                    font = copy(cell.font)
                    font.bold = True
                    cell.font = font

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


def _safe_for_global(df: pd.DataFrame) -> pd.DataFrame:
    """Conserva productos útiles para pricing aunque no expongan SKU/URL."""
    if df.empty:
        return df
    has_product = df["product"].fillna("").astype(str).str.strip().ne("")
    has_price = df["price_current"].notna()
    return df.loc[has_product & has_price].copy()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test completo Farmacias San Pablo con Chrome"
    )
    parser.add_argument("--category", default="all")
    parser.add_argument("--location", default="san-pablo-online")
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Ejecuta Chrome sin ventana. Por defecto el test es visible.",
    )
    parser.add_argument(
        "--profile-dir",
        default=".san_pablo_profile",
        help="Perfil persistente de Chrome cuando el script abre navegador.",
    )
    parser.add_argument(
        "--debugger-address",
        default=None,
        help=(
            "Chrome abierto manualmente con remote debugging, "
            "por ejemplo 127.0.0.1:9223."
        ),
    )
    parser.add_argument(
        "--update-consolidated",
        action="store_true",
        help="Actualiza el master sólo cuando se solicita explícitamente.",
    )
    args = parser.parse_args()

    categories = load_categories(
        ROOT / "config" / "farmacias-san-pablo" / "categories.yaml"
    )
    if args.category != "all":
        categories = [x for x in categories if x.id == args.category]
    if not categories:
        raise SystemExit(f"Categoría no encontrada: {args.category}")

    locations = {x.id: x for x in load_locations(ROOT / "config" / "locations.yaml")}
    location = locations.get(args.location)
    if location is None:
        raise SystemExit(f"Ubicación no encontrada: {args.location}")

    all_rows: list[dict] = []
    summaries: list[dict] = []

    print("=" * 68)
    print("FARMACIAS SAN PABLO - TEST COMPLETO")
    print("=" * 68)
    print(f"Categorías : {len(categories)}")
    print(
        "Browser    : "
        + (
            f"manual attach {args.debugger_address}"
            if args.debugger_address
            else "script-managed Chrome"
        )
    )
    print(f"Salida     : {OUTPUT_PATH}")
    print(
        "Consolidado: "
        + (
            str(CONSOLIDATED_PATH)
            if args.update_consolidated
            else "sin cambios"
        )
    )
    print("")

    for index, category in enumerate(categories, start=1):
        print("-" * 68)
        print(f"[{index}/{len(categories)}] {category.id}")
        print(category.url)
        print("-" * 68)

        scraper = FarmaciasSanPabloScraper(
            headless=args.headless,
            max_pages=args.max_pages,
            profile_dir=(
                None if args.debugger_address else args.profile_dir
            ),
            debugger_address=args.debugger_address,
        )

        status = "SUCCESS"
        error = None
        rows: list[dict] = []

        try:
            rows = scraper.scrape_category(category, location)
            meta = scraper.last_meta or {}
            status = str(meta.get("status") or ("SUCCESS" if rows else "EMPTY"))
        except FarmaciasSanPabloBlocked as exc:
            meta = scraper.last_meta or {}
            status = "BLOCKED"
            error = str(exc)
            print(f"BLOCKED [{category.id}]: {exc}")
        except FarmaciasSanPabloNetworkUnavailable as exc:
            meta = scraper.last_meta or {}
            status = "NETWORK_UNAVAILABLE"
            error = str(exc)
            print(f"NETWORK_UNAVAILABLE [{category.id}]: {exc}")
        except Exception as exc:
            meta = scraper.last_meta or {}
            status = "ERROR"
            error = f"{type(exc).__name__}: {exc}"
            print(f"ERROR [{category.id}]: {error}")

        for row in rows:
            row["department"] = category.department
            row["category"] = category.name
            row["subcategory"] = category.subcategory
            row["sub_subcategory"] = category.sub_subcategory
            row["category_id"] = category.id

        df = pd.DataFrame(rows, columns=COLUMNS)
        if not df.empty:
            df = df.drop_duplicates(
                subset=["category_id", "sku", "url", "product", "price_current"],
                keep="last",
            ).reset_index(drop=True)
            all_rows.extend(df.to_dict("records"))

            if args.update_consolidated:
                safe = _safe_for_global(df)
                if not safe.empty:
                    update_consolidated_output(
                        safe,
                        ROOT / CONSOLIDATED_PATH,
                    )

        target = meta.get("target_products")
        products = len(df)

        unique_skus = int(
            df.loc[df["sku"].notna(), "sku"].astype(str).nunique()
        ) if not df.empty else 0
        unique_urls = int(
            df.loc[df["url"].notna(), "url"].astype(str).nunique()
        ) if not df.empty else 0
        price_complete = int(df["price_current"].notna().sum()) if not df.empty else 0
        missing_identifier = int(
            (
                (df["sku"].isna() | df["sku"].astype(str).str.strip().eq(""))
                & (df["url"].isna() | df["url"].astype(str).str.strip().eq(""))
            ).sum()
        ) if not df.empty else 0

        sku_complete = int(
            df["sku"].fillna("").astype(str).str.strip().ne("").sum()
        ) if not df.empty else 0
        url_complete = int(
            df["url"].fillna("").astype(str).str.strip().ne("").sum()
        ) if not df.empty else 0
        availability = (
            df["availability_status"]
            .fillna("UNKNOWN")
            .astype(str)
            .str.strip()
            .str.upper()
        ) if not df.empty else pd.Series(dtype=str)
        available_products = int(availability.eq("AVAILABLE").sum())
        unavailable_products = int(availability.eq("UNAVAILABLE").sum())
        availability_unknown = int(availability.eq("UNKNOWN").sum())

        # Para San Pablo, el objetivo operativo es pricing/promoción.
        # SKU y URL se reportan como métricas informativas, no bloqueantes.
        if products == 0:
            status = "EMPTY"
        elif price_complete < products:
            status = "PARTIAL"
        elif target is not None and products < int(target):
            status = "PARTIAL"
        else:
            status = "SUCCESS"

        summaries.append(
            {
                "category_id": category.id,
                "target_products": target,
                "products_extracted": products,
                "unique_skus": unique_skus,
                "unique_urls": unique_urls,
                "sku_complete": sku_complete,
                "url_complete": url_complete,
                "price_complete": price_complete,
                "available_products": available_products,
                "unavailable_products": unavailable_products,
                "availability_unknown": availability_unknown,
                "missing_identifier": missing_identifier,
                "status": status,
                "error": error,
            }
        )

        _write_test_output(all_rows, summaries)

        print(
            f"RESULT [{category.id}]: status={status} "
            f"target={target} products={products} "
            f"sku={sku_complete}/{products} url={url_complete}/{products} "
            f"price={price_complete}/{products} "
            f"available={available_products} unavailable={unavailable_products} "
            f"unknown={availability_unknown} missing_id={missing_identifier}"
        )
        print(f"Output actualizado: {OUTPUT_PATH}")
        print("")

    print("=" * 68)
    print("RESUMEN FARMACIAS SAN PABLO")
    print("=" * 68)
    for item in summaries:
        print(
            f"{item['category_id']}: {item['status']} "
            f"({item['products_extracted']}/{item['target_products']})"
        )

    print("")
    print(f"Archivo de prueba : {OUTPUT_PATH}")
    print(
        "Consolidado global: "
        + (
            str(ROOT / CONSOLIDATED_PATH)
            if args.update_consolidated
            else "sin cambios"
        )
    )

    successful = all(
        item["status"] == "SUCCESS"
        and int(item["price_complete"]) == int(item["products_extracted"])
        for item in summaries
    )
    return 0 if successful else 2


if __name__ == "__main__":
    raise SystemExit(main())
