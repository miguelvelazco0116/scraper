from __future__ import annotations

import json
import re
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright

BROWSE_ENDPOINT = "https://api.empathy.co/search/v1/query/fda/browse"
CATEGORIES = {
    "congestion-nasal": "https://www.fahorro.com/farmacia/gripa-y-tos/congestion-nasal.html",
    "preservativos": "https://www.fahorro.com/bienestar-sexual/preservativos.html",
    "enjuagues-bucales": "https://www.fahorro.com/cuidado-personal/higiene-bucal/enjuagues-bucales.html",
    "cremas-dentales": "https://www.fahorro.com/cuidado-personal/higiene-bucal/cremas-dentales.html",
}
KNOWN_SKU = "7896009419324"  # Sensodyne Original, ficha oficial usada como control de precio.


def extract_category_id(html: str) -> str | None:
    patterns = [
        r'"categoryId"\s*:\s*"?(\d+)"?',
        r'categoryId\\?"?\s*:\s*\\?"?(\d+)',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, html, flags=re.IGNORECASE)
        if matches:
            # La configuración de Empathy aparece cerca del final; el ID hoja suele ser el último.
            return matches[-1]
    return None


def browse_url(category_id: str, start: int = 0, rows: int = 50) -> str:
    params = {
        "origin": "search_box",
        "start": str(start),
        "rows": str(rows),
        "lang": "es",
        "scope": "desktop",
        "channel": "tl",
        "region": "NACIONAL",
        "browseField": "categoryIds",
        "browseValue": category_id,
    }
    return BROWSE_ENDPOINT + "?" + urlencode(params)


def main() -> int:
    with sync_playwright() as p:
        request = p.request.new_context(extra_http_headers={
            "Accept": "application/json,text/html,*/*;q=0.8",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/139.0.0.0 Safari/537.36",
        })

        category_ids: dict[str, str] = {}
        for category_key, page_url in CATEGORIES.items():
            response = request.get(page_url, timeout=45_000)
            html = response.text()
            category_id = extract_category_id(html)
            print(json.dumps({
                "category": category_key,
                "page_status": response.status,
                "html_bytes": len(html),
                "category_id": category_id,
            }, ensure_ascii=False))
            if category_id:
                category_ids[category_key] = category_id

        all_items: dict[str, list[dict]] = {}
        for category_key, category_id in category_ids.items():
            first = request.get(browse_url(category_id, 0, 50), timeout=45_000)
            data = first.json() if first.ok else {}
            catalog = data.get("catalog") or {}
            pagination = catalog.get("pagination") or {}
            content = list(catalog.get("content") or [])
            total = int(pagination.get("total") or len(content))
            start = len(content)
            while start < total:
                response = request.get(browse_url(category_id, start, 50), timeout=45_000)
                page_data = response.json() if response.ok else {}
                page_content = ((page_data.get("catalog") or {}).get("content") or [])
                if not page_content:
                    break
                content.extend(page_content)
                start += len(page_content)
            all_items[category_key] = content
            print(json.dumps({
                "category": category_key,
                "category_id": category_id,
                "api_status": first.status,
                "api_total": total,
                "downloaded": len(content),
                "sku_complete": sum(bool(x.get("sku")) for x in content),
                "current_price_complete": sum(x.get("currentPrice") is not None for x in content),
                "previous_price_complete": sum(x.get("previousPrice") is not None for x in content),
                "url_key_complete": sum(bool(x.get("ecommUrlKey")) for x in content),
            }, ensure_ascii=False))

        control = None
        for item in all_items.get("cremas-dentales", []):
            if str(item.get("sku")) == KNOWN_SKU:
                control = {
                    "sku": item.get("sku"),
                    "title": item.get("ecommTitle"),
                    "currentPrice": item.get("currentPrice"),
                    "previousPrice": item.get("previousPrice"),
                    "urlKey": item.get("ecommUrlKey"),
                }
                break
        print(json.dumps({"known_sku_control": control}, ensure_ascii=False))
        request.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
