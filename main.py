from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd

from scraper.config import load_categories, load_locations
from scraper.retailers.soriana import SorianaBlocked, SorianaScraper
from scraper.retailers.walmart import WalmartBlocked, WalmartScraper, WalmartStoreContextError
from scraper.retailers.walmart_persistent import WalmartPersistentScraper

COLUMNS = [
    "scrape_timestamp", "retailer", "city", "state", "postal_code", "store", "store_id",
    "department", "category", "subcategory", "sub_subcategory", "category_id", "sku", "brand",
    "product", "price_current", "price_regular", "promotion", "pickup_available",
    "store_context_verified", "url", "price_raw",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Scraper multi-retailer")
    parser.add_argument("--retailer", default="soriana", choices=["soriana", "walmart"])
    parser.add_argument("--category", default="cuidado-bucal")
    parser.add_argument("--location", default=None)
    parser.add_argument("--store", default=None, help="Alias de ubicación para una tienda configurada, ej. sc-toreo")
    parser.add_argument("--profile-dir", default=None, help="Perfil persistente de Playwright para Walmart")
    parser.add_argument("--headed", action="store_true", help="Abrir navegador visible")
    parser.add_argument("--max-load-more", type=int, default=100)
    args = parser.parse_args()

    category_path = f"config/{args.retailer}/categories.yaml"
    category = next((x for x in load_categories(category_path) if x.id == args.category), None)
    location_id = args.store or args.location or ("sc-toreo" if args.retailer == "walmart" else "cdmx")
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
        profile_dir = args.profile_dir or os.getenv("WALMART_USER_DATA_DIR")
        if profile_dir:
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
    else:
        raise SystemExit(f"Retailer no implementado: {args.retailer}")

    df = pd.DataFrame(rows, columns=COLUMNS)
    if not df.empty:
        df = df.drop_duplicates(subset=["sku", "url"], keep="last")
        df = df.sort_values(["brand", "product"], na_position="last").reset_index(drop=True)

    Path("output").mkdir(exist_ok=True)
    stem = f"{args.retailer}_{args.category.replace('-', '_')}_{location_id.replace('-', '_')}"
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
