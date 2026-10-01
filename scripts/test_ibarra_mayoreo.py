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


def run_category(category, location) -> tuple[pd.DataFrame, dict, list[dict], list[dict]]:
    print("")
    print("-" * 72)
    print(f"IBARRA MAYOREO | {category.department}")
    print("-" * 72)
    print(f"category_id : {category.id}")
    print(f"subcategoría: {category.subcategory}")
    print(f"URL         : {category.url}")
    print("Precio      : SIEMPRE presentación CAJA")
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
        df = (
            df.drop_duplicates(subset=["sku", "url"], keep="last")
            .sort_values(["brand", "product"], na_position="last")
            .reset_index(drop=True)
        )

    meta = scraper.last_meta or {}
    target = meta.get("target_products")
    last_page = meta.get("last_page")
    links = meta.get("product_links")
    discovery_complete = bool(meta.get("discovery_complete"))
    without_box = meta.get("products_without_box_price") or []
    parse_errors = meta.get("parse_errors") or []

    summary = {
        "retailer": "Ibarra Mayoreo",
        "department": category.department,
        "category": category.name,
        "subcategory": category.subcategory,
        "category_id": category.id,
        "target_products": target,
        "last_page": last_page,
        "product_links": links,
        "discovery_complete": discovery_complete,
        "products_with_box_price": len(df),
        "sku_complete": int(
            df["sku"].fillna("").astype(str).str.strip().ne("").sum()
        ) if not df.empty else 0,
        "price_complete": int(df["price_current"].notna().sum()) if not df.empty else 0,
        "url_complete": int(
            df["url"].fillna("").astype(str).str.strip().ne("").sum()
        ) if not df.empty else 0,
        "products_without_box_price": len(without_box),
        "parse_errors": len(parse_errors),
        "status": status,
        "error": error,
    }

    print("RESULTADO")
    print(f"department                 : {category.department}")
    print(f"status                     : {status}")
    print(f"target_products            : {target}")
    print(f"last_page                  : {last_page}")
    print(f"product_links              : {links}")
    print(f"discovery_complete         : {discovery_complete}")
    print(f"products_with_box_price    : {len(df)}")
    print(f"sku_complete               : {summary['sku_complete']}")
    print(f"price_complete             : {summary['price_complete']}")
    print(f"url_complete               : {summary['url_complete']}")
    print(f"products_without_box_price : {len(without_box)}")
    print(f"parse_errors               : {len(parse_errors)}")
    if error:
        print(f"error                      : {error}")

    no_box_rows = [
        {
            "department": category.department,
            "category_id": category.id,
            **item,
        }
        for item in without_box
    ]
    error_rows = [
        {
            "department": category.department,
            "category_id": category.id,
            **item,
        }
        for item in parse_errors
    ]

    return df, summary, no_box_rows, error_rows


def main() -> int:
    categories = load_categories(
        ROOT / "config" / "ibarra-mayoreo" / "categories.yaml"
    )
    location = next(
        x
        for x in load_locations(ROOT / "config" / "locations.yaml")
        if x.id == "ibarra-online"
    )

    print("=" * 72)
    print("IBARRA MAYOREO - TEST COMPLETO")
    print("=" * 72)
    print("Fuentes a extraer:")
    for category in categories:
        print(f"  - {category.department} > {category.subcategory}")
    print("Regla de precio: SIEMPRE presentación CAJA")
    print(f"Salida: {OUTPUT_PATH}")

    frames: list[pd.DataFrame] = []
    summaries: list[dict] = []
    no_box_rows: list[dict] = []
    error_rows: list[dict] = []

    for category in categories:
        df, summary, category_no_box, category_errors = run_category(
            category,
            location,
        )
        frames.append(df)
        summaries.append(summary)
        no_box_rows.extend(category_no_box)
        error_rows.extend(category_errors)

    concentrated = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=COLUMNS)
    )
    summary_df = pd.DataFrame(summaries)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_PATH, engine="openpyxl") as writer:
        concentrated.to_excel(writer, index=False, sheet_name="Concentrado")
        summary_df.to_excel(writer, index=False, sheet_name="Resumen")
        pd.DataFrame(no_box_rows).to_excel(
            writer,
            index=False,
            sheet_name="SinPrecioCaja",
        )
        pd.DataFrame(error_rows).to_excel(
            writer,
            index=False,
            sheet_name="Errores",
        )

    print("")
    print("=" * 72)
    print("RESUMEN IBARRA MAYOREO")
    print("=" * 72)
    if not summary_df.empty:
        print(
            summary_df[
                [
                    "department",
                    "category_id",
                    "status",
                    "target_products",
                    "last_page",
                    "product_links",
                    "discovery_complete",
                    "products_with_box_price",
                    "products_without_box_price",
                    "parse_errors",
                ]
            ].to_string(index=False)
        )

    print("")
    print(f"Total filas con precio CAJA: {len(concentrated)}")
    print(f"Archivo: {OUTPUT_PATH}")
    print("Diagnósticos: diagnostics\\ibarra_mayoreo_*")

    acceptable = True
    for summary in summaries:
        acceptable = acceptable and (
            summary["status"] in {"SUCCESS", "PARTIAL"}
            and bool(summary["discovery_complete"])
            and summary["products_with_box_price"] > 0
            and summary["price_complete"] == summary["products_with_box_price"]
            and summary["parse_errors"] == 0
        )

    return 0 if acceptable else 2


if __name__ == "__main__":
    raise SystemExit(main())
