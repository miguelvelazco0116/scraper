from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from main import COLUMNS
from scraper.config import load_categories, load_locations
from scraper.retailers.bodega_aurrera import (
    BodegaAurreraBlocked,
    BodegaAurreraNetworkUnavailable,
    BodegaAurreraScraper,
)


OUTPUT_PATH = ROOT / "output" / "bodega_aurrera_test.xlsx"


def run_category(category, location):
    print("")
    print("-" * 72)
    print(f"BODEGA AURRERA | {category.id}")
    print("-" * 72)
    print(f"Departamento : {category.department}")
    print(f"Categoría    : {category.name}")
    print(f"Subcategoría : {category.subcategory}")
    print(f"URL          : {category.url}")
    print("")

    scraper = BodegaAurreraScraper(
        headless=False,
        browser_channel="chrome",
        max_pages=30,
        wait_ms=1200,
        manual_verification_timeout_ms=180_000,
    )

    rows = []
    status = "SUCCESS"
    error = None

    try:
        rows = scraper.scrape_category(category, location)
        status = str(scraper.run_meta.get("status") or "SUCCESS")
    except BodegaAurreraBlocked as exc:
        status = "BLOCKED"
        error = str(exc)
    except BodegaAurreraNetworkUnavailable as exc:
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

    meta = scraper.run_meta or {}
    summary = {
        "retailer": "Bodega Aurrera",
        "department": category.department,
        "category": category.name,
        "subcategory": category.subcategory,
        "category_id": category.id,
        "status": status,
        "products": len(df),
        "sku_complete": int(
            df["sku"].fillna("").astype(str).str.strip().ne("").sum()
        ) if not df.empty else 0,
        "price_complete": int(df["price_current"].notna().sum())
        if not df.empty else 0,
        "url_complete": int(
            df["url"].fillna("").astype(str).str.strip().ne("").sum()
        ) if not df.empty else 0,
        "blocked_detected": bool(meta.get("blocked_detected")),
        "manual_verification_required": bool(
            meta.get("manual_verification_required")
        ),
        "manual_verification_resolved": bool(
            meta.get("manual_verification_resolved")
        ),
        "pages_visited": len(meta.get("pages") or []),
        "error": error,
    }

    pages = [
        {"category_id": category.id, **item}
        for item in (meta.get("pages") or [])
    ]
    extraction = [
        {"category_id": category.id, **item}
        for item in (meta.get("card_extraction") or [])
    ]

    print("RESULTADO")
    for key in (
        "status",
        "products",
        "sku_complete",
        "price_complete",
        "url_complete",
        "blocked_detected",
        "manual_verification_required",
        "manual_verification_resolved",
        "pages_visited",
    ):
        print(f"{key:29}: {summary[key]}")
    if error:
        print(f"{'error':29}: {error}")

    return df, summary, pages, extraction


def main() -> int:
    categories = [
        x
        for x in load_categories(
            ROOT / "config" / "bodega-aurrera" / "categories.yaml"
        )
    ]
    location = next(
        x
        for x in load_locations(ROOT / "config" / "locations.yaml")
        if x.id == "bodega-aurrera-online"
    )

    print("=" * 72)
    print("BODEGA AURRERA - TEST CATEGORIAS ACTIVAS")
    print("=" * 72)
    print("Si aparece 'Verifica tu identidad', completa manualmente la")
    print("verificación en Chrome. El scraper NO la evade.")
    print("")
    print("Categorías:")
    for category in categories:
        print(f"  - {category.id}: {category.department} > {category.name}")

    frames = []
    summaries = []
    pages = []
    extraction = []

    for category in categories:
        df, summary, category_pages, category_extraction = run_category(
            category,
            location,
        )
        frames.append(df)
        summaries.append(summary)
        pages.extend(category_pages)
        extraction.extend(category_extraction)

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
        pd.DataFrame(pages).to_excel(writer, index=False, sheet_name="Paginas")
        pd.DataFrame(extraction).to_excel(
            writer,
            index=False,
            sheet_name="Extraccion",
        )

    print("")
    print("=" * 72)
    print("RESUMEN BODEGA AURRERA")
    print("=" * 72)
    print(
        summary_df[
            [
                "department",
                "category_id",
                "status",
                "products",
                "sku_complete",
                "price_complete",
                "url_complete",
                "blocked_detected",
                "manual_verification_required",
                "manual_verification_resolved",
                "pages_visited",
            ]
        ].to_string(index=False)
    )
    print("")
    print(f"Total filas: {len(concentrated)}")
    print(f"Archivo: {OUTPUT_PATH}")
    print("Diagnósticos: diagnostics\\bodega_aurrera_*")

    acceptable = True
    for summary in summaries:
        acceptable = acceptable and (
            summary["status"] == "SUCCESS"
            and summary["products"] > 0
            and summary["sku_complete"] == summary["products"]
            and summary["price_complete"] == summary["products"]
            and summary["url_complete"] == summary["products"]
        )

    return 0 if acceptable else 2


if __name__ == "__main__":
    raise SystemExit(main())
