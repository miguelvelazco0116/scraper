from __future__ import annotations

import argparse
import json
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
from scraper.retailers.farmacias_guadalajara import (
    FarmaciasGuadalajaraBlocked,
    FarmaciasGuadalajaraScraper,
)

OUTPUT = ROOT / "output" / "farmacias_guadalajara_phase2_test.xlsx"
DIAG = ROOT / "diagnostics" / "phase2" / "farmacias_guadalajara"
COVERAGE_THRESHOLD = 0.90


def normalized_path(value: str) -> str:
    return urlsplit(value or "").path.rstrip("/").casefold()


def find_fg_page(browser):
    candidates = []
    for context in browser.contexts:
        for page in context.pages:
            if "farmaciasguadalajara.com" in (page.url or "").casefold():
                candidates.append(page)
    return candidates[-1] if candidates else None


def navigate_same_manual_session(page, category, scraper) -> str:
    scraper._assert_not_blocked(page)

    if normalized_path(page.url) == normalized_path(category.url):
        return "already_on_category"

    page.evaluate("(url) => window.location.assign(url)", category.url)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=45_000)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(2_500)
    scraper._assert_not_blocked(page)

    if normalized_path(page.url) != normalized_path(category.url):
        raise RuntimeError(
            f"No fue posible llegar a {category.id}; URL actual={page.url}"
        )
    return "manual_session_navigation"


def normalize_frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    for column in COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    frame = frame[COLUMNS]
    if frame.empty:
        return frame
    return (
        frame.drop_duplicates(subset=["sku", "url"], keep="last")
        .sort_values(["category_id", "brand", "product"], na_position="last")
        .reset_index(drop=True)
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prueba Fase 2 de Farmacias Guadalajara usando Chrome "
            "abierto manualmente."
        )
    )
    parser.add_argument("--category", default="all")
    parser.add_argument("--cdp-url", required=True)
    args = parser.parse_args()

    categories = load_categories(
        ROOT / "config" / "farmacias-guadalajara" / "categories.yaml"
    )
    if args.category != "all":
        categories = [item for item in categories if item.id == args.category]
        if not categories:
            raise SystemExit(f"Categoría no encontrada: {args.category}")

    location = next(
        item
        for item in load_locations(ROOT / "config" / "locations.yaml")
        if item.id == "fg-online"
    )

    DIAG.mkdir(parents=True, exist_ok=True)
    frames: list[pd.DataFrame] = []
    summaries: list[dict] = []

    print("=" * 80)
    print("FASE 2 - FARMACIAS GUADALAJARA SOBRE CHROME MANUAL")
    print("=" * 80)
    print(f"Cobertura mínima aceptable: {COVERAGE_THRESHOLD:.0%}")
    print(f"CDP                       : {args.cdp_url}")
    print("El script NO abrirá ni cerrará Chrome.")
    print("")

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(args.cdp_url)
        except Exception as exc:
            print("ERROR: no fue posible conectarse al Chrome abierto.")
            print(f"{type(exc).__name__}: {exc}")
            return 10

        page = find_fg_page(browser)
        if page is None:
            print(
                "ERROR: abre Farmacias Guadalajara en Chrome "
                "antes de ejecutar el test."
            )
            return 11

        scraper = FarmaciasGuadalajaraScraper(
            headless=False,
            max_load_more=100,
        )

        print(f"Pestaña detectada: {page.url}")

        for index, category in enumerate(categories, start=1):
            print("-" * 80)
            print(f"[{index}/{len(categories)}] {category.id}")
            print(category.url)

            rows: list[dict] = []
            meta: dict = {}
            error = None
            status = "COMPLETE"
            navigation_method = None

            try:
                navigation_method = navigate_same_manual_session(
                    page,
                    category,
                    scraper,
                )
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
            target = meta.get("target_products")
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
                if coverage is None:
                    notes.append("target no disponible")
                elif coverage < COVERAGE_THRESHOLD:
                    notes.append(
                        f"cobertura {coverage:.1%} < {COVERAGE_THRESHOLD:.0%}"
                    )
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
                "retailer": "Farmacias Guadalajara",
                "category_id": category.id,
                "status": status,
                "target_products": target,
                "products": products,
                "coverage": coverage,
                "coverage_threshold": COVERAGE_THRESHOLD,
                "product_links": meta.get("product_links"),
                "sku_complete": sku_complete,
                "price_complete": price_complete,
                "url_complete": url_complete,
                "navigation_method": navigation_method,
                "quality_notes": "; ".join(notes),
            }
            summaries.append(summary)

            (DIAG / f"{category.id}_phase2.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )

            print(
                f"  -> {status} target={target} products={products} "
                f"coverage={coverage if coverage is not None else 'n/d'} "
                f"sku={sku_complete} price={price_complete} url={url_complete}"
            )

            if status in {"BLOCKED", "ERROR"}:
                print("Se detiene para no forzar la sesión manual.")
                break

        # No browser.close(): Chrome pertenece al usuario.

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
    print("RESUMEN FARMACIAS GUADALAJARA")
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
