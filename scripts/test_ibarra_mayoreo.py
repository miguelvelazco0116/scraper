from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from main import COLUMNS
from scraper.config import load_categories, load_locations
from scraper.retailers.ibarra_mayoreo import (
    IbarraMayoreoBlocked,
    IbarraMayoreoNetworkUnavailable,
    IbarraMayoreoScraper,
)


OUTPUT_PATH = ROOT / "output" / "ibarra_mayoreo_test.xlsx"


def main() -> int:
    category = next(
        x
        for x in load_categories(
            ROOT / "config" / "ibarra-mayoreo" / "categories.yaml"
        )
        if x.id == "detergentes-lavatrastes-jab"
    )
    location = next(
        x
        for x in load_locations(ROOT / "config" / "locations.yaml")
        if x.id == "ibarra-online"
    )

    print("=" * 72)
    print("IBARRA MAYOREO - TEST DETergentes, lavatrastes y jab")
    print("=" * 72)
    print(f"Departamento : {category.department}")
    print(f"Subcategoría : {category.subcategory}")
    print(f"URL          : {category.url}")
    print("Regla precio : SIEMPRE precio de presentación CAJA")
    print(f"Salida       : {OUTPUT_PATH}")
    print("")

    scraper = IbarraMayoreoScraper(
        headless=False,
        browser_channel="chrome",
        max_pages=30,
        wait_ms=700,
    )

    rows: list[dict] = []
    status = "SUCCESS"
    error = None

    try:
        rows = scraper.scrape_category(category, location)
        status = str(scraper.last_meta.get("status") or "SUCCESS")
    except IbarraMayoreoBlocked as exc:
        status = "BLOCKED"
        error = str(exc)
    except IbarraMayoreoNetworkUnavailable as exc:
        status = "NETWORK_UNAVAILABLE"
        error = str(exc)
    except Exception as exc:
        status = "ERROR"
        error = f"{type(exc).__name__}: {exc}"

    df = pd.DataFrame(rows, columns=COLUMNS)
    if not df.empty:
        df = df.drop_duplicates(
            subset=["sku", "url"],
            keep="last",
        ).sort_values(
            ["brand", "product"],
            na_position="last",
        ).reset_index(drop=True)

    meta = scraper.last_meta or {}
    target = meta.get("target_products")
    links = meta.get("product_links")
    without_box = meta.get("products_without_box_price") or []
    parse_errors = meta.get("parse_errors") or []

    summary = pd.DataFrame(
        [
            {
                "retailer": "Ibarra Mayoreo",
                "category_id": category.id,
                "target_products": target,
                "product_links": links,
                "products_with_box_price": len(df),
                "sku_complete": int(
                    df["sku"].fillna("").astype(str).str.strip().ne("").sum()
                ) if not df.empty else 0,
                "price_complete": int(
                    df["price_current"].notna().sum()
                ) if not df.empty else 0,
                "url_complete": int(
                    df["url"].fillna("").astype(str).str.strip().ne("").sum()
                ) if not df.empty else 0,
                "products_without_box_price": len(without_box),
                "parse_errors": len(parse_errors),
                "status": status,
                "error": error,
            }
        ]
    )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_PATH, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Concentrado")
        summary.to_excel(writer, index=False, sheet_name="Resumen")
        pd.DataFrame(without_box).to_excel(
            writer,
            index=False,
            sheet_name="SinPrecioCaja",
        )
        pd.DataFrame(parse_errors).to_excel(
            writer,
            index=False,
            sheet_name="Errores",
        )

    print("")
    print("=" * 72)
    print("RESULTADO IBARRA MAYOREO")
    print("=" * 72)
    print(f"status                     : {status}")
    print(f"target_products            : {target}")
    print(f"product_links              : {links}")
    print(f"products_with_box_price    : {len(df)}")
    print(f"sku_complete               : {summary.iloc[0]['sku_complete']}")
    print(f"price_complete             : {summary.iloc[0]['price_complete']}")
    print(f"url_complete               : {summary.iloc[0]['url_complete']}")
    print(f"products_without_box_price : {len(without_box)}")
    print(f"parse_errors               : {len(parse_errors)}")
    if error:
        print(f"error                      : {error}")
    print("")
    print(f"Archivo: {OUTPUT_PATH}")
    print("Diagnósticos: diagnostics\\ibarra_mayoreo_*")

    acceptable = (
        status in {"SUCCESS", "PARTIAL"}
        and len(df) > 0
        and int(summary.iloc[0]["price_complete"]) == len(df)
    )
    return 0 if acceptable else 2


if __name__ == "__main__":
    raise SystemExit(main())
