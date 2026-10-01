from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from main import COLUMNS
from scraper.config import load_categories, load_locations
from scraper.retailers.la_comer import (
    LaComerBlocked,
    LaComerNetworkUnavailable,
    LaComerScraper,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Test de categorías La Comer")
    parser.add_argument(
        "--category",
        default="detergentes-suavizantes",
        help="ID de categoría configurada para La Comer",
    )
    args = parser.parse_args()

    categories = {
        x.id: x
        for x in load_categories(ROOT / "config" / "la-comer" / "categories.yaml")
    }
    category = categories.get(args.category)
    if category is None:
        raise SystemExit(
            f"Categoría La Comer no encontrada: {args.category}. "
            f"Disponibles: {', '.join(sorted(categories))}"
        )

    location = next(
        x
        for x in load_locations(ROOT / "config" / "locations.yaml")
        if x.id == "la-comer-online-287"
    )

    output_path = ROOT / "output" / f"la_comer_{category.id}_test.xlsx"

    print("=" * 68)
    print(f"LA COMER - TEST {category.subcategory or category.name}")
    print("=" * 68)
    print(f"category_id : {category.id}")
    print(f"departamento: {category.department}")
    print(f"categoría   : {category.name}")
    print(f"subcategoría: {category.subcategory}")
    print(f"URL base    : {category.url}")
    print(f"Contexto    : succId={location.store_id}")
    print(f"Salida      : {output_path}")
    print("")

    scraper = LaComerScraper(
        headless=False,
        browser_channel="chrome",
        max_scroll_rounds=100,
    )

    rows: list[dict] = []
    status = "SUCCESS"
    error = None

    try:
        rows = scraper.scrape_category(category, location)
    except LaComerBlocked as exc:
        status = "BLOCKED"
        error = str(exc)
    except LaComerNetworkUnavailable as exc:
        status = "NETWORK_UNAVAILABLE"
        error = str(exc)
    except Exception as exc:
        status = "ERROR"
        error = f"{type(exc).__name__}: {exc}"

    df = pd.DataFrame(rows, columns=COLUMNS)
    if not df.empty:
        sku = df["sku"].fillna("").astype(str).str.strip()
        url = df["url"].fillna("").astype(str).str.strip()
        has_id = sku.ne("") | url.ne("")
        with_id = df.loc[has_id].drop_duplicates(
            subset=["sku", "url"],
            keep="last",
        )
        without_id = df.loc[~has_id].drop_duplicates(
            subset=["product", "price_current", "price_raw"],
            keep="last",
        )
        df = pd.concat([with_id, without_id], ignore_index=True)
        df = df.sort_values(
            ["brand", "product"],
            na_position="last",
        ).reset_index(drop=True)

    if status == "SUCCESS" and df.empty:
        status = "EMPTY"

    summary = pd.DataFrame(
        [
            {
                "retailer": "La Comer",
                "department": category.department,
                "category": category.name,
                "subcategory": category.subcategory,
                "category_id": category.id,
                "status": status,
                "products": len(df),
                "sku_complete": int(
                    df["sku"].fillna("").astype(str).str.strip().ne("").sum()
                ) if not df.empty else 0,
                "price_complete": int(
                    df["price_current"].notna().sum()
                ) if not df.empty else 0,
                "url_complete": int(
                    df["url"].fillna("").astype(str).str.strip().ne("").sum()
                ) if not df.empty else 0,
                "error": error,
            }
        ]
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Concentrado")
        summary.to_excel(writer, index=False, sheet_name="Resumen")

    meta = scraper.run_meta or {}
    print("RESULTADO")
    print(f"status         : {status}")
    print(f"products       : {len(df)}")
    print(f"sku_complete   : {summary.iloc[0]['sku_complete']}")
    print(f"price_complete : {summary.iloc[0]['price_complete']}")
    print(f"url_complete   : {summary.iloc[0]['url_complete']}")
    print("")
    for query, info in (meta.get("queries") or {}).items():
        print(
            f"{query}: ui={info.get('used_search_ui')} "
            f"cards={info.get('cards_discovered')} "
            f"kept={info.get('rows_kept')} "
            f"url={info.get('url')}"
        )
    if error:
        print(f"error: {error}")
    print("")
    print(f"Archivo: {output_path}")
    print("Diagnósticos: diagnostics\\la_comer_*")

    return 0 if status == "SUCCESS" and len(df) > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
