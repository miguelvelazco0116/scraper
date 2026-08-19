from __future__ import annotations

import json
import re
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright

BROWSE_ENDPOINT = "https://api.empathy.co/search/v1/query/fda/browse"
CATEGORY_ID = "8196"
KNOWN_SKU = "7896009419324"
KNOWN_PAGE = "https://www.fahorro.com/crema-dental-sensodyne-original-90-g.html"


def browse_url(start: int = 0, rows: int = 50) -> str:
    params = {
        "origin": "search_box",
        "start": str(start),
        "rows": str(rows),
        "lang": "es",
        "scope": "desktop",
        "channel": "tl",
        "region": "NACIONAL",
        "browseField": "categoryIds",
        "browseValue": CATEGORY_ID,
    }
    return BROWSE_ENDPOINT + "?" + urlencode(params)


def main() -> int:
    with sync_playwright() as p:
        request = p.request.new_context(extra_http_headers={
            "Accept": "application/json,text/html,*/*;q=0.8",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/139.0.0.0 Safari/537.36",
        })
        response = request.get(browse_url(0, 150), timeout=45_000)
        data = response.json() if response.ok else {}
        content = ((data.get("catalog") or {}).get("content") or [])
        item = next((x for x in content if str(x.get("sku")) == KNOWN_SKU), None)
        print(json.dumps({"empathy_status": response.status, "empathy_item": item}, ensure_ascii=False))

        page_response = request.get(KNOWN_PAGE, timeout=45_000)
        html = page_response.text()
        snippets = []
        for needle in ("67.50", "79.00", "79", "90.00", KNOWN_SKU):
            pos = html.find(needle)
            if pos >= 0:
                snippets.append({"needle": needle, "snippet": html[max(0, pos-800):pos+1200]})
        price_amounts = sorted(set(re.findall(r'"amount"\s*:\s*([0-9]+(?:\.[0-9]+)?)', html)))
        print(json.dumps({
            "page_status": page_response.status,
            "html_bytes": len(html),
            "amount_values": price_amounts[:30],
            "snippets": snippets,
        }, ensure_ascii=False))
        request.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
