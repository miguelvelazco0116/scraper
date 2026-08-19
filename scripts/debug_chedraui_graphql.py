from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from scraper.config import load_categories, load_locations
from scraper.retailers.chedraui_polanco import ChedrauiScraper


OUT = Path("diagnostics/chedraui_graphql_capture.json")


def main() -> int:
    category = next(x for x in load_categories("config/chedraui/categories.yaml") if x.id == "lavanderia")
    location = next(x for x in load_locations() if x.id == "chedraui-polanco")
    scraper = ChedrauiScraper(headless=True, max_pages=1)
    captured: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="es-MX", viewport={"width": 1440, "height": 1000})
        page = context.new_page()

        def on_request(request):
            url = request.url
            if "graphql" not in url.lower() and "search" not in url.lower():
                return
            if "productSearch" not in (request.post_data or "") and "graphql" not in url.lower():
                return
            captured.append(
                {
                    "kind": "request",
                    "method": request.method,
                    "url": url,
                    "post_data": request.post_data,
                    "resource_type": request.resource_type,
                }
            )

        def on_response(response):
            request = response.request
            url = request.url
            if "graphql" not in url.lower():
                return
            entry = {
                "kind": "response",
                "status": response.status,
                "url": url,
                "method": request.method,
                "post_data": request.post_data,
            }
            try:
                text = response.text()
                if "productSearch" in text or "recordsFiltered" in text or "products" in text:
                    entry["body"] = text[:20000]
                else:
                    return
            except Exception as exc:
                entry["body_error"] = type(exc).__name__
            captured.append(entry)

        page.on("request", on_request)
        page.on("response", on_response)

        response = page.goto(category.url, wait_until="domcontentloaded", timeout=120_000)
        scraper._assert_not_blocked(page, response.status if response else None)
        page.wait_for_timeout(1_500)
        verified, method = scraper._verify_store_context(page, location)
        if not verified:
            verified, method = scraper._try_select_store_ui(page, location)
        if not verified:
            raise RuntimeError("No se pudo seleccionar Polanco para la captura")

        for page_number in (1, 17, 19, 20):
            page.goto(scraper._paged_url(category.url, page_number), wait_until="domcontentloaded", timeout=120_000)
            page.wait_for_timeout(2_000)

        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(captured, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"captured={len(captured)} file={OUT}")
        context.close()
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
