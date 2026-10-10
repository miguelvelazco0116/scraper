from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
from openpyxl.styles import Font
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from main import COLUMNS
from scraper.config import load_categories, load_locations
from scraper.retailers.soriana import PRODUCT_SELECTOR, SorianaBlocked, SorianaScraper

OUTPUT = ROOT / "output" / "soriana_manual_phase2_test.xlsx"
DIAG = ROOT / "diagnostics" / "phase2" / "soriana_manual"


def normalize_url(value: str) -> str:
    parts = urlsplit(value or "")
    path = parts.path.rstrip("/") + "/"
    return f"{parts.scheme}://{parts.netloc}{path}".casefold()


def find_soriana_page(browser):
    candidates = []
    for context in browser.contexts:
        for page in context.pages:
            if "soriana.com" in (page.url or "").casefold():
                candidates.append(page)
    return candidates[-1] if candidates else None


def target_count(page) -> int | None:
    try:
        text = page.locator("body").inner_text(timeout=8_000)
    except Exception:
        return None

    matches = re.findall(
        r"\b([0-9][0-9,]{0,5})\s+productos?\b",
        text,
        flags=re.IGNORECASE,
    )
    values = []
    for raw in matches:
        try:
            values.append(int(raw.replace(",", "")))
        except ValueError:
            pass
    return max(values) if values else None


def navigate_same_manual_session(page, category, scraper) -> None:
    if normalize_url(page.url) != normalize_url(category.url):
        page.evaluate("(url) => window.location.assign(url)", category.url)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=45_000)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(2_500)

    scraper._assert_not_blocked(page)
    page.wait_for_selector(
        PRODUCT_SELECTOR,
        state="attached",
        timeout=30_000,
    )


def normalize_frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    for column in COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    frame = frame[COLUMNS]
    if frame.empty:
        return frame

    sku = frame["sku"].fillna("").astype(str).str.strip()
    url = frame["url"].fillna("").astype(str).str.strip()
    with_id = sku.ne("") | url.ne("")
    identified = frame.loc[with_id].drop_duplicates(
        subset=["category_id", "sku", "url"],
        keep="last",
    )
    unidentified = frame.loc[~with_id].drop_duplicates(
        subset=["category_id", "product", "price_current"],
        keep="last",
    )
    return (
        pd.concat([identified, unidentified], ignore_index=True)
        .sort_values(["category_id", "brand", "product"], na_position="last")
        .reset_index(drop=True)
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fase 2 Soriana usando Chrome abierto manualmente."
    )
    parser.add_argument("--cdp-url", required=True)
    parser.add_argument("--category", default="all")
    parser.add_argument("--max-pages", type=int, default=100)
    args = parser.parse_args()

    categories = load_categories(
        ROOT / "config" / "soriana" / "categories.yaml"
    )
    if args.category != "all":
        categories = [item for item in categories if item.id == args.category]
        if not categories:
            raise SystemExit(f"Categoría no encontrada: {args.category}")

    location = next(
        item
        for item in load_locations(ROOT / "config" / "locations.yaml")
        if item.id == "cdmx"
    )

    DIAG.mkdir(parents=True, exist_ok=True)
    frames: list[pd.DataFrame] = []
    summaries: list[dict] = []

    print("=" * 80)
    print("FASE 2 - SORIANA SOBRE CHROME MANUAL")
    print("=" * 80)
    print(f"Categorías: {len(categories)}")
    print(f"CDP       : {args.cdp_url}")
    print("El script NO abrirá ni cerrará Chrome.")
    print("")

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(args.cdp_url)
        except Exception as exc:
            print("ERROR: no fue posible conectarse al Chrome abierto.")
            print(f"{type(exc).__name__}: {exc}")
            return 10

        page = find_soriana_page(browser)
        if page is None:
            print("ERROR: abre Soriana en Chrome antes de ejecutar el test.")
            return 11

        scraper = SorianaScraper(
            headless=False,
            diagnostics_dir=DIAG,
            max_load_more=args.max_pages,
            wait_ms=1200,
        )
        page.on("response", scraper._capture_grid_response)

        print(f"Pestaña detectada: {page.url}")

        for index, category in enumerate(categories, start=1):
            print("-" * 80)
            print(f"[{index}/{len(categories)}] {category.id}")
            print(category.url)

            rows: list[dict] = []
            error = None
            status = "COMPLETE"
            target = None
            total_pages = None

            try:
                navigate_same_manual_session(page, category, scraper)
                target = target_count(page)
                total_pages = scraper._total_pages(page)
                rows = scraper._collect_pages(page, category, location)
                scraper._save_diagnostics(page, f"phase2_{category.id}")
            except SorianaBlocked as exc:
                status = "BLOCKED"
                error = str(exc)
            except Exception as exc:
                status = "ERROR"
                error = f"{type(exc).__name__}: {exc}"

            frame = normalize_frame(rows)
            products = len(frame)
            coverage = (
                products / int(target)
                if target not in (None, 0)
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

            notes: list[str] = []
            if error:
                notes.append(error)
            else:
                if products <= 0:
                    notes.append("sin productos")
                if target is not None and products < int(target):
                    notes.append(f"cobertura {products}/{target}")
                if sku_complete < products:
                    notes.append(f"sku {sku_complete}/{products}")
                if price_complete < products:
                    notes.append(f"precio {price_complete}/{products}")
                if url_complete < products:
                    notes.append(f"url {url_complete}/{products}")
                status = "COMPLETE" if not notes else "PARTIAL"

            if not frame.empty:
                frames.append(frame)

            summary = {
                "retailer": "Soriana",
                "category_id": category.id,
                "status": status,
                "target_products": target,
                "products": products,
                "coverage": coverage,
                "total_pages": total_pages,
                "sku_complete": sku_complete,
                "price_complete": price_complete,
                "url_complete": url_complete,
                "quality_notes": "; ".join(notes),
            }
            summaries.append(summary)
            (DIAG / f"{category.id}_phase2.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )

            print(
                f"  -> {status} products={products} target={target} "
                f"pages={total_pages} sku={sku_complete} "
                f"price={price_complete} url={url_complete}"
            )

            if status in {"BLOCKED", "ERROR"}:
                print("Se detiene para no forzar la sesión manual.")
                break

        # No browser.close(): la sesión pertenece al usuario.

    concentrated = (
        pd.concat(frames, ignore_index=True)
        if frames
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
    print("=" * 80)
    print("RESUMEN SORIANA MANUAL")
    print("=" * 80)
    if not summary_df.empty:
        print(
            summary_df[
                [
                    "category_id",
                    "status",
                    "target_products",
                    "products",
                    "coverage",
                    "total_pages",
                    "sku_complete",
                    "price_complete",
                    "url_complete",
                    "quality_notes",
                ]
            ].to_string(index=False)
        )
    print(f"Output: {OUTPUT}")

    return (
        0
        if len(summary_df) == len(categories)
        and not summary_df.empty
        and summary_df["status"].eq("COMPLETE").all()
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
