from __future__ import annotations

import json
import re
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

CATEGORY_URL = "https://www.fahorro.com/cuidado-personal/higiene-bucal/cremas-dentales.html"
TERMS = [
    "Infinite_EmpathySearch",
    "search-list-component",
    "searchResultsEndpointUrl",
    "browseField",
    "browseValue",
    "customerGroup",
    "currentPrice",
    "facetCategory",
    "categoryId",
    "api.empathy.co",
]


def context_snippets(text: str, term: str, radius: int = 900) -> list[str]:
    snippets: list[str] = []
    lower = text.lower()
    needle = term.lower()
    start = 0
    while len(snippets) < 8:
        pos = lower.find(needle, start)
        if pos < 0:
            break
        snippets.append(text[max(0, pos - radius): min(len(text), pos + len(term) + radius)])
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

        script_urls = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
        interesting = [
            urljoin(CATEGORY_URL, src)
            for src in script_urls
            if "requirejs-map" in src or "requirejs-min-resolver" in src or "requirejs-config" in src
        ]
        print(json.dumps({"requirejs_files": interesting}, ensure_ascii=False))

        resolved_candidates: list[str] = []
        for script_url in interesting:
            try:
                response = request.get(script_url, timeout=45_000)
                text = response.text()
                print("REQUIREJS_FILE_BEGIN")
                print(json.dumps({"status": response.status, "url": script_url, "bytes": len(text)}, ensure_ascii=False))
                for term in TERMS:
                    snippets = context_snippets(text, term)
                    if snippets:
                        print(json.dumps({"term": term, "snippets": snippets}, ensure_ascii=False))
                for match in re.finditer(r'[^"\']*Infinite_EmpathySearch[^"\']*search-list-component[^"\']*', text, re.IGNORECASE):
                    resolved_candidates.append(match.group(0))
                print("REQUIREJS_FILE_END")
            except Exception as exc:
                print(json.dumps({"url": script_url, "error": repr(exc)}, ensure_ascii=False))

        print(json.dumps({"resolved_candidates": resolved_candidates[:20]}, ensure_ascii=False))
        request.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
