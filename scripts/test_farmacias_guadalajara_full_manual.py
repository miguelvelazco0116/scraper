from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
from openpyxl.styles import Font
from playwright.sync_api import sync_playwright

from main import COLUMNS
from scraper.config import load_categories, load_locations
from scraper.retailers.farmacias_guadalajara import (
    FarmaciasGuadalajaraBlocked,
    FarmaciasGuadalajaraScraper,
)
from scripts.scrape_farmacias_guadalajara_manual_session import (
    find_fg_page,
    navigate_to_category,
    normalize_frame,
)

OUTPUT = ROOT / "output" / "farmacias_guadalajara_full_test.xlsx"
DIAG_DIR = ROOT / "diagnostics" / "farmacias_guadalajara_full"

CATEGORY_ORDER = [
    "vias-respiratorias",
    "lavanderia",
    "cuidado-bucal",
    "preservativos",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prueba completa de Farmacias Guadalajara sobre Chrome existente."
    )
    parser.add_argument("--cdp-url", required=True)
    parser.add_argument("--max-load-more", type=int, default=100)
    args = parser.parse_args()

    categories = {
        item.id: item
        for item in load_categories(
            ROOT / "config" / "farmacias-guadalajara" / "categories.yaml"
        )
    }
    location = next(
        item
        for item in load_locations(ROOT / "config" / "locations.yaml")
        if item.id == "fg-online"
    )

    DIAG_DIR.mkdir(parents=True, exist_ok=True)
    all_frames: list[pd.DataFrame] = []
    summaries: list[dict] = []

    print("=" * 82)
    print("FARMACIAS GUADALAJARA - PRUEBA COMPLETA")
    print("=" * 82)
    print(f"Categorías : {', '.join(CATEGORY_ORDER)}")
    print(f"CDP        : {args.cdp_url}")
    print("")

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(args.cdp_url)
        page = find_fg_page(browser)
        if page is None:
            print("ERROR: no se encontró una pestaña abierta de Farmacias Guadalajara.")
            return 1

        print(f"Pestaña inicial: {page.url}")
        print("")

        for index, category_id in enumerate(CATEGORY_ORDER, start=1):
            category = categories[category_id]
            scraper = FarmaciasGuadalajaraScraper(
                headless=False,
                max_load_more=args.max_load_more,
            )

            print("-" * 82)
            print(f"[{index}/{len(CATEGORY_ORDER)}] {category_id}")

            status = "SUCCESS"
            error = None
            rows: list[dict] = []
            meta: dict = {}

            try:
                navigation_method = navigate_to_category(
                    page,
                    category,
                    scraper,
                )
                print(f"Navegación : {navigation_method}")
                print(f"URL        : {page.url}")

                rows, meta = scraper.extract_loaded_page(
                    page,
                    category,
                    location,
                    expand=True,
                    context_method="manual_browser_online_catalog",
                )
            except FarmaciasGuadalajaraBlocked as exc:
                status = "BLOCKED"
                error = str(exc)
            except Exception as exc:
                status = "ERROR"
                error = f"{type(exc).__name__}: {exc}"

            frame = normalize_frame(rows)
            if not frame.empty:
                all_frames.append(frame)

            target = meta.get("target_products")
            products = len(frame)
            coverage = (
                round(products / target, 4)
                if isinstance(target, int) and target > 0
                else None
            )
            sku_complete = int(
                frame["sku"].fillna("").astype(str).str.strip().ne("").sum()
            ) if not frame.empty else 0
            price_complete = int(
                frame["price_current"].notna().sum()
            ) if not frame.empty else 0
            url_complete = int(
                frame["url"].fillna("").astype(str).str.strip().ne("").sum()
            ) if not frame.empty else 0

            if status == "SUCCESS":
                if products <= 0:
                    status = "EMPTY"
                elif coverage is not None and coverage < 0.95:
                    status = "PARTIAL"

            summary = {
                "retailer": "Farmacias Guadalajara",
                "category_id": category_id,
                "status": status,
                "target_products": target,
                "product_links": meta.get("product_links"),
                "products": products,
                "coverage": coverage,
                "sku_complete": sku_complete,
                "price_complete": price_complete,
                "url_complete": url_complete,
                "expansion_rounds": len(meta.get("expansion_trace") or []),
                "error": error,
            }
            summaries.append(summary)

            (DIAG_DIR / f"{category_id}_meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            print(
                f"Resultado   : {status} | target={target} | "
                f"products={products} | coverage={coverage} | "
                f"sku={sku_complete} | price={price_complete} | url={url_complete}"
            )
            if error:
                print(f"Error       : {error}")

            if status in {"BLOCKED", "ERROR"}:
                print("La prueba se detiene para no forzar la sesión.")
                break

    concentrated = (
        pd.concat(all_frames, ignore_index=True)
        if all_frames
        else pd.DataFrame(columns=COLUMNS)
    )
    summary_df = pd.DataFrame(summaries)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        concentrated.to_excel(writer, index=False, sheet_name="Concentrado")
        summary_df.to_excel(writer, index=False, sheet_name="Resumen")
        for sheet_name in ("Concentrado", "Resumen"):
            ws = writer.book[sheet_name]
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for cell in ws[1]:
                cell.font = Font(bold=True)

    print("")
    print("=" * 82)
    print("RESUMEN FINAL - FARMACIAS GUADALAJARA")
    print("=" * 82)
    if not summary_df.empty:
        print(summary_df.to_string(index=False))
    print(f"Filas totales: {len(concentrated)}")
    print(f"Output      : {OUTPUT}")

    bad = summary_df["status"].isin(["BLOCKED", "ERROR", "EMPTY"])
    return 2 if bool(bad.any()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
