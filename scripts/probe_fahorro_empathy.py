from __future__ import annotations

import json
import re
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright

CATEGORY_ID = "8196"
BROWSE_ENDPOINT = "https://api.empathy.co/search/v1/query/fda/browse"
BASE = "https://www.fahorro.com/static/version1785883877/frontend/Magento/base/default/Infinite_EmpathySearch/js/action/"
FILES = [
    BASE + "get-additional-data.min.js",
    "https://www.fahorro.com/static/version1785883877/frontend/Omnipro/Farma_Theme/default/Infinite_EmpathySearch/js/action/get-additional-data-mixin.min.js",
    "https://www.fahorro.com/static/version1785883877/frontend/Magento/base/default/Infinite_EmpathySearch/js/utils/normalize-sku.min.js",
]


def main() -> int:
    with sync_playwright() as p:
        request = p.request.new_context(extra_http_headers={
            "Accept": "application/json,text/javascript,*/*;q=0.8",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/139.0.0.0 Safari/537.36",
        })

        params = {
            "origin": "search_box",
            "start": "0",
            "rows": "12",
            "lang": "es",
            "scope": "desktop",
            "channel": "tl",
            "region": "NACIONAL",
            "browseField": "categoryIds",
            "browseValue": CATEGORY_ID,
        }
        url = BROWSE_ENDPOINT + "?" + urlencode(params)
        response = request.get(url, timeout=45_000)
        body = response.text()
        print("EXACT_BROWSE_BEGIN")
        print(json.dumps({"status": response.status, "url": url, "body": body[:30000]}, ensure_ascii=False))
        if response.ok:
            data = response.json()
            catalog = data.get("catalog") or {}
            content = catalog.get("content") or []
            print(json.dumps({
                "pagination": catalog.get("pagination"),
                "content_count": len(content),
                "item_keys": sorted(content[0].keys()) if content else [],
                "sample_items": content[:3],
            }, ensure_ascii=False))
        print("EXACT_BROWSE_END")

        for js_url in FILES:
            print("ADDITIONAL_JS_BEGIN")
            try:
                js_response = request.get(js_url, timeout=45_000)
                text = js_response.text()
                print(json.dumps({"status": js_response.status, "url": js_url, "bytes": len(text)}, ensure_ascii=False))
                if js_response.ok:
                    print(text[:30000])
                    urls = re.findall(r"['\"]([^'\"]*(?:additional|product|price)[^'\"]*)['\"]", text, flags=re.IGNORECASE)
                    if urls:
                        print(json.dumps({"url_hints": urls[:50]}, ensure_ascii=False))
            except Exception as exc:
                print(json.dumps({"url": js_url, "error": repr(exc)}, ensure_ascii=False))
            print("ADDITIONAL_JS_END")

        request.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
