from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from main import COLUMNS, CONSOLIDATED_PATH, update_consolidated_output
from scraper.config import load_categories, load_locations
from scraper.retailers.farmacias_del_ahorro import (
    FarmaciasDelAhorroBlocked,
    FarmaciasDelAhorroNetworkUnavailable,
    FarmaciasDelAhorroScraper,
)


def run_category(category, location, *, headless: bool, max_pages: int) -> tuple[str, int]:
    scraper = FarmaciasDelAhorroScraper(headless=headless, max_pages=max_pages)
    try:
        rows = scraper.scrape_category(category, location)
    except FarmaciasDelAhorroBlocked as exc:
        print(f"BLOCKED [{category.id}]: {exc}")
        return "BLOCKED", 0
    except FarmaciasDelAhorroNetworkUnavailable as exc:
        print(f"NETWORK_UNAVAILABLE [{category.id}]: {exc}")
        return "NETWORK_UNAVAILABLE", 0

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
        update_consolidated_output(df)

    sku_complete = int(df["sku"].notna().sum()) if not df.empty else 0
    price_complete = int(df["price_current"].notna().sum()) if not df.empty else 0
    url_complete = int(df["url"].notna().sum()) if not df.empty else 0
    print(
        f"RESULT [{category.id}]: products={len(df)} sku={sku_complete} "
        f"price={price_complete} url={url_complete}"
    )
    if df.empty:
        return "EMPTY", 0
    return "SUCCESS", len(df)


def main() -> int:
    parser = argparse.ArgumentParser(description="Scraper Farmacias del Ahorro")
    parser.add_argument("--category", default="all")
    parser.add_argument("--location", default="fahorro-online")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--fresh", action="store_true", help="Borra el concentrado antes de iniciar")
    args = parser.parse_args()

    categories = load_categories("config/farmacias-del-ahorro/categories.yaml")
    if args.category != "all":
        categories = [x for x in categories if x.id == args.category]
    if not categories:
        raise SystemExit(f"Categoría no encontrada: {args.category}")

    locations = {x.id: x for x in load_locations()}
    location = locations.get(args.location)
    if location is None:
        raise SystemExit(f"Ubicación no encontrada: {args.location}")

    if args.fresh:
        Path(CONSOLIDATED_PATH).unlink(missing_ok=True)

    results: list[tuple[str, str, int]] = []
    for category in categories:
        print(f"Scraping Farmacias del Ahorro / {category.id}")
        status, products = run_category(
            category,
            location,
            headless=not args.headed,
            max_pages=args.max_pages,
        )
        results.append((category.id, status, products))

    print("\nRESUMEN FARMACIAS DEL AHORRO")
    for category_id, status, products in results:
        print(f"{category_id}: {status} ({products})")
    print(f"Concentrado: {CONSOLIDATED_PATH}")

    return 0 if all(status == "SUCCESS" for _, status, _ in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
