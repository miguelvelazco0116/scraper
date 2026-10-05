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
from scraper.retailers.bodega_aurrera import (
    BodegaAurreraBlocked,
    BodegaAurreraNetworkUnavailable,
    BodegaAurreraScraper,
)

OUTPUT = ROOT / "output" / "bodega_aurrera_full_test.xlsx"
DIAG_DIR = ROOT / "diagnostics" / "bodega_aurrera_full"

CATEGORY_ORDER = [
    "cuidado-bucal",
    "cuidado-de-la-ropa",
]


def find_bodega_page(browser):
    candidates = []
    for context in browser.contexts:
        for page in context.pages:
            url = (page.url or "").casefold()
            if "bodegaaurrera.com.mx" in url:
                candidates.append(page)
    return candidates[-1] if candidates else None


def normalize_frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    for column in COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    return frame[COLUMNS].copy()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prueba completa de Bodega Aurrera sobre Chrome existente."
    )
    parser.add_argument("--cdp-url", required=True)
    parser.add_argument("--max-pages", type=int, default=30)
    args = parser.parse_args()

    categories = {
        item.id: item
        for item in load_categories(
            ROOT / "config" / "bodega-aurrera" / "categories.yaml"
        )
    }
    location = next(
        item
        for item in load_locations(ROOT / "config" / "locations.yaml")
        if item.id == "bodega-aurrera-online"
    )

    DIAG_DIR.mkdir(parents=True, exist_ok=True)
    all_frames: list[pd.DataFrame] = []
    summaries: list[dict] = []

    print("=" * 82)
    print("BODEGA AURRERA - PRUEBA COMPLETA")
    print("=" * 82)
    print(f"Categorías : {', '.join(CATEGORY_ORDER)}")
    print(f"CDP        : {args.cdp_url}")
    print("")

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(args.cdp_url)
        page = find_bodega_page(browser)
        if page is None:
            print("ERROR: no se encontró una pestaña abierta de Bodega Aurrera.")
            return 1

        print(f"Pestaña inicial: {page.url}")
        print("")

        for index, category_id in enumerate(CATEGORY_ORDER, start=1):
            category = categories[category_id]
            scraper = BodegaAurreraScraper(
                headless=False,
                browser_channel="chrome",
                max_pages=args.max_pages,
            )

            print("-" * 82)
            print(f"[{index}/{len(CATEGORY_ORDER)}] {category_id}")

            status = "SUCCESS"
            error = None
            rows: list[dict] = []

            try:
                rows = scraper.scrape_category_on_page(
                    page,
                    category,
                    location,
                    navigate_to_category=True,
                )
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

            frame = normalize_frame(rows)
            if not frame.empty:
                all_frames.append(frame)

            availability = (
                frame["availability_status"]
                .fillna("UNKNOWN")
                .astype(str)
                .str.upper()
                if not frame.empty
                else pd.Series(dtype=str)
            )

            summary = {
                "retailer": "Bodega Aurrera",
                "category_id": category_id,
                "status": status,
                "products": len(frame),
                "sku_complete": int(
                    frame["sku"].fillna("").astype(str).str.strip().ne("").sum()
                ) if not frame.empty else 0,
                "price_complete": int(
                    frame["price_current"].notna().sum()
                ) if not frame.empty else 0,
                "url_complete": int(
                    frame["url"].fillna("").astype(str).str.strip().ne("").sum()
                ) if not frame.empty else 0,
                "available_products": int(availability.eq("AVAILABLE").sum()),
                "unavailable_products": int(availability.eq("UNAVAILABLE").sum()),
                "availability_unknown": int(availability.eq("UNKNOWN").sum()),
                "sources_discovered": scraper.run_meta.get("sources_discovered"),
                "pagination_verified": scraper.run_meta.get("pagination_verified"),
                "error": error,
            }
            summaries.append(summary)

            (DIAG_DIR / f"{category_id}_meta.json").write_text(
                json.dumps(scraper.run_meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            print(
                f"Resultado   : {status} | products={len(frame)} | "
                f"sku={summary['sku_complete']} | "
                f"price={summary['price_complete']} | "
                f"url={summary['url_complete']} | "
                f"pagination={summary['pagination_verified']}"
            )
            if error:
                print(f"Error       : {error}")

            if status in {"BLOCKED", "NETWORK_UNAVAILABLE", "ERROR"}:
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
    print("RESUMEN FINAL - BODEGA AURRERA")
    print("=" * 82)
    if not summary_df.empty:
        print(summary_df.to_string(index=False))
    print(f"Filas totales: {len(concentrated)}")
    print(f"Output      : {OUTPUT}")

    bad = summary_df["status"].isin(
        ["BLOCKED", "NETWORK_UNAVAILABLE", "ERROR", "EMPTY"]
    )
    return 2 if bool(bad.any()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
