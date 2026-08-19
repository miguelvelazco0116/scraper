from __future__ import annotations

import json
import re
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

CATEGORY_URL = "https://www.fahorro.com/cuidado-personal/higiene-bucal/cremas-dentales.html"
MODULE_PATH = "Infinite_EmpathySearch/js/view/search-list-component.js"
TERMS = [
    "searchResultsEndpointUrl",
    "browseField",
    "browseValue",
    "customerGroup",
    "currentPrice",
    "sort",
    "facetCategory",
    "categoryId",
    "scope",
    "lang",
    "start",
    "rows",
    "api.empathy.co",
]


def context_snippets(text: str, term: str, radius: int = 700) -> list[str]:
    snippets: list[str] = []
    lower = text.lower()
    needle = term.lower()
    start = 0
    while len(snippets) < 6:
        pos = lower.find(needle, start)
        if pos < 0:
            break
        left = max(0, pos - radius)
        right = min(len(text), pos + len(term) + radius)
        snippets.append(text[left:right])
        start = pos + len(term)
    return snippets


def main() -> int:
    with sync_playwright() as p:
        request = p.request.new_context(extra_http_headers={
            "Accept": "text/html,application/xhtml+xml,application/javascript,*/*;q=0.8",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/139.0.0.0 Safari/537.36",
        })
        page_response = request.get(CATEGORY_URL, timeout=45_000)
        html = page_response.text()
        print(json.dumps({"page_status": page_response.status, "page_bytes": len(html)}, ensure_ascii=False))

        version_match = re.search(r"(/static/version\d+/frontend/Omnipro/Farma_Theme/default/)", html)
        candidates: list[str] = []
        if version_match:
            candidates.append(urljoin(CATEGORY_URL, version_match.group(1) + MODULE_PATH))
        candidates.extend([
            "https://www.fahorro.com/static/frontend/Omnipro/Farma_Theme/default/" + MODULE_PATH,
            "https://www.fahorro.com/static/version1785883877/frontend/Omnipro/Farma_Theme/default/" + MODULE_PATH,
        ])

        seen: set[str] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            try:
                response = request.get(candidate, timeout=45_000)
                text = response.text()
                print("MODULE_CANDIDATE_BEGIN")
                print(json.dumps({"status": response.status, "url": candidate, "bytes": len(text)}, ensure_ascii=False))
                if response.ok:
                    for term in TERMS:
                        snippets = context_snippets(text, term)
                        if snippets:
                            print(json.dumps({"term": term, "snippets": snippets}, ensure_ascii=False))
                    print("MODULE_HEAD", text[:5000])
                print("MODULE_CANDIDATE_END")
                if response.ok and len(text) > 500:
                    break
            except Exception as exc:
                print(json.dumps({"url": candidate, "error": repr(exc)}, ensure_ascii=False))
        request.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
