from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

URL = "https://www.fahorro.com/cuidado-personal/higiene-bucal/cremas-dentales.html"
ENDPOINT = "https://api.empathy.co/search/v1/query/fda/browse"


def main() -> int:
    candidates = [
        {"browseField": "categoryId", "browseValue": "8196", "start": "0", "rows": "3"},
        {"browseField": "facetCategory", "browseValue": "8196", "start": "0", "rows": "3"},
        {"browseField": "category", "browseValue": "8196", "start": "0", "rows": "3"},
        {"browseField": "categoryId", "browseValue": "8196", "start": "0", "rows": "3", "scope": "NACIONAL"},
        {"browseField": "categoryId", "browseValue": "8196", "start": "0", "rows": "3", "customerGroup": "NACIONAL"},
        {"browseField": "facetCategory", "browseValue": "8196", "start": "0", "rows": "3", "scope": "NACIONAL"},
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="es-MX")
        page = context.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=45_000)

        print("EMPATHY_DIRECT_BEGIN")
        for index, params in enumerate(candidates, start=1):
            url = ENDPOINT + "?" + urlencode(params)
            try:
                response = context.request.get(url, timeout=30_000)
                text = response.text()
                print(json.dumps({
                    "candidate": index,
                    "status": response.status,
                    "url": url,
                    "body": text[:2500],
                }, ensure_ascii=False))
            except Exception as exc:
                print(json.dumps({"candidate": index, "url": url, "error": repr(exc)}, ensure_ascii=False))
        print("EMPATHY_DIRECT_END")

        context.close()
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
