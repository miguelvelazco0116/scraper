from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from scraper.config import load_categories, load_locations
from scraper.retailers.chedraui_polanco import ChedrauiScraper


OUT = Path("diagnostics/chedraui_graphql_capture.json")
MISSING = Path("diagnostics/chedraui_missing_340_359.json")


def rewrite_range(url: str, start: int, end: int) -> str:
    parts = urlsplit(url)
    qs = parse_qs(parts.query, keep_blank_values=True)
    extensions = json.loads(qs["extensions"][0])
    encoded = extensions.get("variables")
    variables = json.loads(base64.b64decode(encoded).decode("utf-8"))
    variables["from"] = start
    variables["to"] = end
    extensions["variables"] = base64.b64encode(
        json.dumps(variables, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    flat = {k: values[-1] for k, values in qs.items()}
    flat["extensions"] = json.dumps(extensions, separators=(",", ":"))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(flat), parts.fragment))


def main() -> int:
    category = next(x for x in load_categories("config/chedraui/categories.yaml") if x.id == "lavanderia")
    location = next(x for x in load_locations() if x.id == "chedraui-polanco")
    scraper = ChedrauiScraper(headless=True, max_pages=1)
    captured: list[dict] = []
    product_search_urls: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="es-MX", viewport={"width": 1440, "height": 1000})
        page = context.new_page()

        def on_request(request):
            url = request.url
            if "operationName=productSearchV3" in url:
                product_search_urls.append(url)
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

        if not product_search_urls:
            raise RuntimeError("No se capturó productSearchV3")
        missing_url = rewrite_range(product_search_urls[0], 340, 359)
        api_response = context.request.get(missing_url, timeout=120_000)
        payload = {
            "status": api_response.status,
            "url": missing_url,
            "body": api_response.json(),
        }
        MISSING.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        products = payload["body"].get("data", {}).get("productSearch", {}).get("products", [])
        print(f"captured={len(captured)} missing_products={len(products)} status={api_response.status}")
        context.close()
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
