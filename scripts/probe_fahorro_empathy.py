from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

ENDPOINT = "https://api.empathy.co/search/v1/query/fda/browse"


def main() -> int:
    candidates = [
        {"lang": "es", "browseField": "categoryId", "browseValue": "8196", "start": "0", "rows": "3"},
        {"lang": "es", "browseField": "facetCategory", "browseValue": "8196", "start": "0", "rows": "3"},
        {"lang": "es", "browseField": "category", "browseValue": "8196", "start": "0", "rows": "3"},
        {"lang": "es", "browseField": "categoryId", "browseValue": "8196", "start": "0", "rows": "3", "scope": "NACIONAL"},
        {"lang": "es", "browseField": "facetCategory", "browseValue": "8196", "start": "0", "rows": "3", "scope": "NACIONAL"},
        {"lang": "es", "browseField": "categoryId", "browseValue": "8196", "start": "0", "rows": "3", "customerGroup": "NACIONAL"},
    ]

    with sync_playwright() as p:
        request = p.request.new_context(extra_http_headers={"Accept": "application/json"})
        print("EMPATHY_DIRECT_BEGIN")
        for index, params in enumerate(candidates, start=1):
            url = ENDPOINT + "?" + urlencode(params)
            try:
                response = request.get(url, timeout=30_000)
                text = response.text()
                print(json.dumps({
                    "candidate": index,
                    "status": response.status,
                    "url": url,
                    "body": text[:5000],
                }, ensure_ascii=False))
            except Exception as exc:
                print(json.dumps({"candidate": index, "url": url, "error": repr(exc)}, ensure_ascii=False))
        print("EMPATHY_DIRECT_END")
        request.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
