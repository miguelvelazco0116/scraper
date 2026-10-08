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
from playwright.sync_api import sync_playwright

from main import COLUMNS, update_consolidated_output
from scraper.config import load_categories, load_locations
from scraper.retailers.farmacias_guadalajara import (
    FarmaciasGuadalajaraBlocked,
    FarmaciasGuadalajaraScraper,
)

DEFAULT_CDP_URL = "http://127.0.0.1:9223"
OUTPUT = ROOT / "output" / "farmacias_guadalajara_manual_session.xlsx"


def normalize_url(value: str) -> str:
    parts = urlsplit(value)
    path = parts.path.rstrip("/")
    return f"{parts.scheme}://{parts.netloc}{path}".casefold()


def normalized_path(value: str) -> str:
    return urlsplit(value).path.rstrip("/").casefold()


def navigate_to_category(page, category, scraper) -> str:
    """Selecciona automáticamente la categoría desde la sesión ya abierta."""
    scraper._assert_not_blocked(page)

    target_path = normalized_path(category.url)
    if normalized_path(page.url) == target_path:
        return "already_on_category"

    # Primero intenta usar un enlace real ya presente en el storefront.
    anchors = page.locator("a[href]")
    total = anchors.count()
    for index in range(total):
        anchor = anchors.nth(index)
        try:
            href = anchor.get_attribute("href")
        except Exception:
            continue
        if not href:
            continue
        if normalized_path(href) != target_path:
            continue
        try:
            anchor.scroll_into_view_if_needed(timeout=3_000)
        except Exception:
            pass
        try:
            anchor.click(timeout=10_000)
            page.wait_for_load_state("domcontentloaded", timeout=30_000)
        except Exception:
            # Algunos menús tienen links ocultos; se reutiliza el href del
            # propio storefront dentro de la misma sesión existente.
            page.evaluate("(url) => window.location.assign(url)", category.url)
            page.wait_for_load_state("domcontentloaded", timeout=30_000)

        page.wait_for_timeout(2_000)
        scraper._assert_not_blocked(page)
        if normalized_path(page.url) == target_path:
            return "storefront_link"

    # Fallback: la categoría no estaba materializada en el DOM del homepage.
    # Se navega en la misma pestaña/sesión que abrió el usuario.
    page.evaluate("(url) => window.location.assign(url)", category.url)
    page.wait_for_load_state("domcontentloaded", timeout=30_000)
    page.wait_for_timeout(2_000)
    scraper._assert_not_blocked(page)

    if normalized_path(page.url) != target_path:
        raise RuntimeError(
            f"No fue posible llegar a la categoría {category.id}; "
            f"URL actual={page.url}"
        )
    return "session_navigation"


def find_fg_page(browser):
    candidates = []
    for context in browser.contexts:
        for page in context.pages:
            if "farmaciasguadalajara.com" in (page.url or "").casefold():
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
        description="Extrae Farmacias Guadalajara desde una pestaña Chrome abierta manualmente."
    )
    parser.add_argument("--category", required=True)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument("--max-load-more", type=int, default=100)
    parser.add_argument("--output", default=str(OUTPUT))
    parser.add_argument("--update-consolidated", action="store_true")
    args = parser.parse_args()

    categories = {
        item.id: item
        for item in load_categories(
            ROOT / "config" / "farmacias-guadalajara" / "categories.yaml"
        )
    }
    category = categories.get(args.category)
    if category is None:
        raise SystemExit(f"Categoría no encontrada: {args.category}")

    location = next(
        item
        for item in load_locations(ROOT / "config" / "locations.yaml")
        if item.id == "fg-online"
    )

    scraper = FarmaciasGuadalajaraScraper(
        headless=False,
        max_load_more=args.max_load_more,
    )

    print("=" * 76)
    print("FARMACIAS GUADALAJARA - SCRAPING SOBRE CHROME MANUAL")
    print("=" * 76)
    print(f"Categoría esperada : {category.id}")
    print(f"URL esperada       : {category.url}")
    print(f"CDP                : {args.cdp_url}")
    print("")

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(args.cdp_url)
        except Exception as exc:
            print("ERROR: no fue posible conectarse al Chrome manual.")
            print(f"{type(exc).__name__}: {exc}")
            return 1

        page = find_fg_page(browser)
        if page is None:
            print("ERROR: no se encontró ninguna pestaña de Farmacias Guadalajara.")
            return 1

        current = page.url
        print(f"Pestaña detectada  : {current}")

        try:
            navigation_method = navigate_to_category(
                page,
                category,
                scraper,
            )
        except FarmaciasGuadalajaraBlocked as exc:
            print(f"BLOCKED_DURING_NAVIGATION: {exc}")
            return 2
        except Exception as exc:
            print(f"NAVIGATION_ERROR: {type(exc).__name__}: {exc}")
            return 7

        print(f"Navegación         : {navigation_method}")
        print(f"Categoría abierta  : {page.url}")

        try:
            rows, meta = scraper.extract_loaded_page(
                page,
                category,
                location,
                expand=True,
                context_method="manual_browser_online_catalog",
            )
        except FarmaciasGuadalajaraBlocked as exc:
            print(f"BLOCKED: {exc}")
            return 2
        except Exception as exc:
            print(f"ERROR_DURING_SCRAPE: {type(exc).__name__}: {exc}")
            return 1

        frame = normalize_frame(rows)
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = ROOT / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)

        diagnostics_dir = ROOT / "diagnostics" / "farmacias_guadalajara_manual"
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        (diagnostics_dir / f"{category.id}_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            frame.to_excel(writer, index=False, sheet_name="Concentrado")
            summary = pd.DataFrame(
                [{
                    "retailer": "Farmacias Guadalajara",
                    "category_id": category.id,
                    "target_products": meta.get("target_products"),
                    "product_links": meta.get("product_links"),
                    "products": len(frame),
                    "sku_complete": int(
                        frame["sku"].fillna("").astype(str).str.strip().ne("").sum()
                    ),
                    "price_complete": int(frame["price_current"].notna().sum()),
                    "url_complete": int(
                        frame["url"].fillna("").astype(str).str.strip().ne("").sum()
                    ),
                }]
            )
            summary.to_excel(writer, index=False, sheet_name="Resumen")
            for sheet_name in ("Concentrado", "Resumen"):
                ws = writer.book[sheet_name]
                ws.freeze_panes = "A2"
                ws.auto_filter.ref = ws.dimensions
                for cell in ws[1]:
                    cell.font = Font(bold=True)

        if args.update_consolidated and not frame.empty:
            update_consolidated_output(
                frame,
                ROOT / "output" / "concentrado_scraper.xlsx",
            )

        print("")
        print("RESULTADO")
        print("-" * 76)
        print(f"Target publicado   : {meta.get('target_products')}")
        print(f"Links detectados   : {meta.get('product_links')}")
        print(f"Productos extraídos: {len(frame)}")
        print(f"SKU completos      : {int(frame['sku'].fillna('').astype(str).str.strip().ne('').sum())}")
        print(f"Precios completos  : {int(frame['price_current'].notna().sum())}")
        print(f"URLs completas     : {int(frame['url'].fillna('').astype(str).str.strip().ne('').sum())}")
        trace = meta.get("expansion_trace") or []
        print(f"Rondas expansion   : {len(trace)}")
        for item in trace:
            print(
                "  ronda "
                f"{item.get('round')}: "
                f"{item.get('before')} -> {item.get('after')} | "
                f"clicked={item.get('clicked')} | "
                f"{item.get('reason') or item.get('click_method') or ''}"
            )
        print(
            "Diagnostico        : "
            f"{diagnostics_dir / (category.id + '_meta.json')}"
        )
        print(f"Output             : {output_path}")
        print(
            "Consolidado         : "
            + ("actualizado" if args.update_consolidated else "sin cambios")
        )

        # No se cierra Chrome; la sesión pertenece al usuario.

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
