from __future__ import annotations

import json

from playwright.sync_api import sync_playwright

BASE = "https://www.fahorro.com/static/version1785883877"
FILES = [
    BASE + "/frontend/Magento/base/default/Infinite_EmpathySearch/js/action/fetch-search-results.min.js",
    BASE + "/frontend/Omnipro/Farma_Theme/default/Infinite_EmpathySearch/js/action/fetch-search-results-mixin.min.js",
    BASE + "/frontend/Magento/base/default/Infinite_EmpathySearch/js/view/search-list-component.min.js",
    BASE + "/frontend/Omnipro/Farma_Theme/default/Infinite_EmpathySearch/js/view/search-list-component-mixin.min.js",
    BASE + "/frontend/Magento/base/default/Infinite_EmpathySearch/js/action/get-current-scope.min.js",
    BASE + "/frontend/Magento/base/default/Infinite_EmpathySearch/js/action/get-store-param.min.js",
    BASE + "/frontend/Magento/base/default/Infinite_EmpathySearch/js/action/get-channel-param.min.js",
]
TERMS = [
    "browseField", "browseValue", "customerGroup", "currentPrice", "filter", "filters",
    "facetCategory", "categoryId", "scope", "store", "channel", "lang", "start", "rows",
    "sort", "params", "URLSearchParams", "searchResultsEndpointUrl",
]


def snippets(text: str, term: str, radius: int = 1200) -> list[str]:
    out: list[str] = []
    lower = text.lower()
    needle = term.lower()
    start = 0
    while len(out) < 8:
        pos = lower.find(needle, start)
        if pos < 0:
            break
        out.append(text[max(0, pos-radius):min(len(text), pos+len(term)+radius)])
        start = pos + len(term)
    return out


def main() -> int:
    with sync_playwright() as p:
        request = p.request.new_context(extra_http_headers={
            "Accept": "application/javascript,text/javascript,*/*;q=0.8",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/139.0.0.0 Safari/537.36",
        })
        for url in FILES:
            print("JS_FILE_BEGIN")
            try:
                response = request.get(url, timeout=45_000)
                text = response.text()
                print(json.dumps({"status": response.status, "url": url, "bytes": len(text)}, ensure_ascii=False))
                if response.ok:
                    print("FULL_JS", text[:40000])
                    for term in TERMS:
                        hits = snippets(text, term)
                        if hits:
                            print(json.dumps({"term": term, "snippets": hits}, ensure_ascii=False))
            except Exception as exc:
                print(json.dumps({"url": url, "error": repr(exc)}, ensure_ascii=False))
            print("JS_FILE_END")
        request.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
