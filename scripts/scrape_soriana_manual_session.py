from __future__ import annotations

import argparse
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
from scraper.retailers.soriana import PRODUCT_SELECTOR, SorianaBlocked, SorianaScraper

DEFAULT_CDP_URL = 'http://127.0.0.1:9222'
OUTPUT = ROOT / 'output' / 'soriana_manual_session.xlsx'


def normalize_url(value: str) -> str:
    parts = urlsplit(value)
    path = parts.path.rstrip('/') + '/'
    return f'{parts.scheme}://{parts.netloc}{path}'.casefold()


def find_soriana_page(browser):
    candidates = []
    for context in browser.contexts:
        for page in context.pages:
            if 'soriana.com' in (page.url or '').casefold():
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
        description='Extrae Soriana desde una pestaña Chrome abierta manualmente.'
    )
    parser.add_argument('--category', required=True)
    parser.add_argument('--cdp-url', default=DEFAULT_CDP_URL)
    parser.add_argument('--max-pages', type=int, default=100)
    parser.add_argument('--update-consolidated', action='store_true')
    args = parser.parse_args()

    categories = {
        item.id: item
        for item in load_categories(ROOT / 'config' / 'soriana' / 'categories.yaml')
    }
    category = categories.get(args.category)
    if category is None:
        raise SystemExit(f'Categoría no encontrada: {args.category}')

    location = next(
        item
        for item in load_locations(ROOT / 'config' / 'locations.yaml')
        if item.id == 'cdmx'
    )

    scraper = SorianaScraper(
        headless=False,
        diagnostics_dir=ROOT / 'diagnostics' / 'soriana_manual_session',
        max_load_more=args.max_pages,
        warmup_homepage=False,
        profile_dir=None,
        circuit_cooldown_seconds=0,
    )

    print('=' * 76)
    print('SORIANA - SCRAPING SOBRE CHROME ABIERTO MANUALMENTE')
    print('=' * 76)
    print(f'Categoría esperada : {category.id}')
    print(f'URL esperada       : {category.url}')
    print(f'CDP                : {args.cdp_url}')
    print('')

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(args.cdp_url)
        except Exception as exc:
            print('ERROR: no fue posible conectarse al Chrome manual.')
            print(f'{type(exc).__name__}: {exc}')
            return 1

        page = find_soriana_page(browser)
        if page is None:
            print('ERROR: no se encontró ninguna pestaña de soriana.com.')
            return 1

        current = page.url
        print(f'Pestaña detectada  : {current}')

        if normalize_url(current) != normalize_url(category.url):
            print('')
            print('CATEGORY_MISMATCH')
            print('Navega manualmente hasta la categoría indicada y vuelve a ejecutar.')
            return 7

        try:
            scraper._assert_not_blocked(page)
        except SorianaBlocked as exc:
            print(f'BLOCKED: {exc}')
            return 2

        try:
            page.wait_for_selector(PRODUCT_SELECTOR, timeout=25_000)
        except Exception:
            print('EMPTY: no se detectó el grid de productos.')
            return 3

        try:
            rows = scraper._collect_pages(page, category, location)
        except SorianaBlocked as exc:
            print(f'BLOCKED_DURING_PAGINATION: {exc}')
            return 2
        except Exception as exc:
            print(f'ERROR_DURING_SCRAPE: {type(exc).__name__}: {exc}')
            return 1

        frame = normalize_frame(rows)
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        with pd.ExcelWriter(OUTPUT, engine='openpyxl') as writer:
            frame.to_excel(writer, index=False, sheet_name='Concentrado')
            status = frame['availability_status'].fillna('UNKNOWN').astype(str).str.upper()
            summary = pd.DataFrame([{
                'retailer': 'Soriana',
                'category_id': category.id,
                'products': len(frame),
                'available_products': int(status.eq('AVAILABLE').sum()),
                'unavailable_products': int(status.eq('UNAVAILABLE').sum()),
                'availability_unknown': int(status.eq('UNKNOWN').sum()),
            }])
            summary.to_excel(writer, index=False, sheet_name='Resumen')
            for sheet_name in ('Concentrado', 'Resumen'):
                ws = writer.book[sheet_name]
                ws.freeze_panes = 'A2'
                ws.auto_filter.ref = ws.dimensions
                for cell in ws[1]:
                    cell.font = Font(bold=True)

        if args.update_consolidated and not frame.empty:
            update_consolidated_output(
                frame,
                ROOT / 'output' / 'concentrado_scraper.xlsx',
            )

        availability = frame['availability_status'].fillna('UNKNOWN').astype(str).str.upper()
        print('')
        print('RESULTADO')
        print('-' * 76)
        print(f'Productos únicos : {len(frame)}')
        print(f'AVAILABLE        : {int(availability.eq("AVAILABLE").sum())}')
        print(f'UNAVAILABLE      : {int(availability.eq("UNAVAILABLE").sum())}')
        print(f'UNKNOWN          : {int(availability.eq("UNKNOWN").sum())}')
        print(f'Output           : {OUTPUT}')
        print(
            'Consolidado       : ' +
            ('actualizado' if args.update_consolidated else 'sin cambios')
        )

        # No llamar browser.close(): Chrome pertenece al usuario.

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
