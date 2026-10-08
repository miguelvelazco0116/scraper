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


def find_bodega_page(browser):
    candidates = []
    for context in browser.contexts:
        for page in context.pages:
            if "bodegaaurrera.com.mx" in (page.url or "").casefold():
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
        description="Extrae Bodega Aurrera desde una pestaña Chrome ya abierta."
    )
    parser.add_argument("--category", required=True)
    parser.add_argument("--cdp-url", required=True)
    parser.add_argument("--max-pages", type=int, default=30)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    categories = {
        item.id: item
        for item in load_categories(
            ROOT / "config" / "bodega-aurrera" / "categories.yaml"
        )
    }
    category = categories.get(args.category)
    if category is None:
        raise SystemExit(f"Categoría no encontrada: {args.category}")

    location = next(
        item
        for item in load_locations(ROOT / "config" / "locations.yaml")
        if item.id == "bodega-aurrera-online"
    )

    scraper = BodegaAurreraScraper(
        headless=False,
        browser_channel="chrome",
        max_pages=args.max_pages,
    )

    print("=" * 76)
    print("BODEGA AURRERA - SCRAPING SOBRE CHROME EXISTENTE")
    print("=" * 76)
    print(f"Categoría : {category.id}")
    print(f"URL       : {category.url}")
    print(f"CDP       : {args.cdp_url}")
    print("")

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(args.cdp_url)
        except Exception as exc:
            print(f"CDP_ERROR: {type(exc).__name__}: {exc}")
            return 1

        page = find_bodega_page(browser)
        if page is None:
            print("ERROR: no se encontró una pestaña abierta de Bodega Aurrera.")
            return 1

        print(f"Pestaña detectada: {page.url}")

        try:
            rows = scraper.scrape_category_on_page(
                page,
                category,
                location,
                navigate_to_category=True,
            )
        except BodegaAurreraBlocked as exc:
            print(f"BLOCKED: {exc}")
            return 2
        except BodegaAurreraNetworkUnavailable as exc:
            print(f"NETWORK_UNAVAILABLE: {exc}")
            return 5
        except Exception as exc:
            print(f"ERROR: {type(exc).__name__}: {exc}")
            return 1

    frame = normalize_frame(rows)
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    diag_dir = ROOT / "diagnostics" / "bodega_aurrera_manual"
    diag_dir.mkdir(parents=True, exist_ok=True)
    (diag_dir / f"{category.id}_meta.json").write_text(
        json.dumps(scraper.run_meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    availability = (
        frame["availability_status"]
        .fillna("UNKNOWN")
        .astype(str)
        .str.upper()
        if not frame.empty
        else pd.Series(dtype=str)
    )

    summary = pd.DataFrame(
        [{
            "retailer": "Bodega Aurrera",
            "category_id": category.id,
            "status": scraper.run_meta.get("status"),
            "products": len(frame),
            "sku_complete": int(
                frame["sku"].fillna("").astype(str).str.strip().ne("").sum()
            ) if not frame.empty else 0,
            "price_complete": int(frame["price_current"].notna().sum())
                if not frame.empty else 0,
            "url_complete": int(
                frame["url"].fillna("").astype(str).str.strip().ne("").sum()
            ) if not frame.empty else 0,
            "available_products": int(availability.eq("AVAILABLE").sum()),
            "unavailable_products": int(availability.eq("UNAVAILABLE").sum()),
            "availability_unknown": int(availability.eq("UNKNOWN").sum()),
            "sources_discovered": scraper.run_meta.get("sources_discovered"),
            "pagination_verified": scraper.run_meta.get("pagination_verified"),
        }]
    )

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="Concentrado")
        summary.to_excel(writer, index=False, sheet_name="Resumen")
        for sheet_name in ("Concentrado", "Resumen"):
            ws = writer.book[sheet_name]
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for cell in ws[1]:
                cell.font = Font(bold=True)

    print("")
    print("RESULTADO")
    print("-" * 76)
    print(f"Status              : {scraper.run_meta.get('status')}")
    print(f"Productos           : {len(frame)}")
    print(f"SKU completos       : {summary.iloc[0]['sku_complete']}")
    print(f"Precios completos   : {summary.iloc[0]['price_complete']}")
    print(f"URLs completas      : {summary.iloc[0]['url_complete']}")
    print(f"Pagination verified : {summary.iloc[0]['pagination_verified']}")
    print(f"Output              : {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
