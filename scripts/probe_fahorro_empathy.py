from __future__ import annotations

import json
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright

ENDPOINT = "https://api.empathy.co/search/v1/query/fda/browse"


def main() -> int:
    base = {"lang": "es", "start": "0", "rows": "3", "sort": ""}
    candidates = [
        {**base, "browseField": "categoryId", "browseValue": "8196"},
        {**base, "browseField": "facetCategory", "browseValue": "8196"},
        {**base, "browseField": "category", "browseValue": "8196"},
        {**base, "browseField": "categoryId", "browseValue": "8196", "scope": "NACIONAL"},
        {**base, "browseField": "facetCategory", "browseValue": "8196", "scope": "NACIONAL"},
        {**base, "browseField": "categoryId", "browseValue": "8196", "customerGroup": "NACIONAL"},
        {"lang": "es", "browseField": "categoryId", "browseValue": "8196", "start": "0", "rows": "3", "sort": "_score desc"},
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
                    "body": text[:7000],
                }, ensure_ascii=False))
            except Exception as exc:
                print(json.dumps({"candidate": index, "url": url, "error": repr(exc)}, ensure_ascii=False))
        print("EMPATHY_DIRECT_END")
        request.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
