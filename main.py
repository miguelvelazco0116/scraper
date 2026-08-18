from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from scraper.config import load_categories, load_locations
from scraper.retailers.soriana import SorianaBlocked, SorianaScraper

COLUMNS = [
    "scrape_timestamp", "retailer", "city", "state", "postal_code", "store",
    "category", "subcategory", "category_id", "sku", "brand", "product",
    "price_current", "price_regular", "promotion", "url", "price_raw",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Scraper multi-retailer")
    parser.add_argument("--retailer", default="soriana", choices=["soriana"])
    parser.add_argument("--category", default="cuidado-bucal")
    parser.add_argument("--location", default="cdmx")
    parser.add_argument("--headed", action="store_true", help="Abrir navegador visible")
    parser.add_argument("--max-load-more", type=int, default=100)
    args = parser.parse_args()

    category_path = f"config/{args.retailer}/categories.yaml"
    category = next((x for x in load_categories(category_path) if x.id == args.category), None)
    location = next((x for x in load_locations() if x.id == args.location), None)
    if category is None:
        raise SystemExit(f"Categoría no encontrada: {args.category}")
    if location is None:
        raise SystemExit(f"Ubicación no encontrada: {args.location}")

    if args.retailer == "soriana":
        scraper = SorianaScraper(headless=not args.headed, max_load_more=args.max_load_more)
        try:
            rows = scraper.scrape_category(category, location)
        except SorianaBlocked as exc:
            print(f"BLOCKED: {exc}")
            return 2
    else:
        raise SystemExit(f"Retailer no implementado: {args.retailer}")

    df = pd.DataFrame(rows, columns=COLUMNS)
    if not df.empty:
        df = df.drop_duplicates(subset=["sku", "url"], keep="last")
        df = df.sort_values(["brand", "product"], na_position="last").reset_index(drop=True)

    Path("output").mkdir(exist_ok=True)
    stem = f"{args.retailer}_{args.category.replace('-', '_')}_{args.location}"
    csv_path = Path(f"output/{stem}.csv")
    xlsx_path = Path(f"output/{stem}.xlsx")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_excel(xlsx_path, index=False, sheet_name=args.retailer.title())

    print(f"Productos únicos: {len(df)}")
    print(f"CSV: {csv_path}")
    print(f"Excel: {xlsx_path}")
    if df.empty:
        print("No se encontraron productos. Revisa diagnostics/.")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
