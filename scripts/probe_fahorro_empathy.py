from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

URL = "https://www.fahorro.com/cuidado-personal/higiene-bucal/cremas-dentales.html"


def main() -> int:
    requests: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="es-MX")
        page = context.new_page()

        def on_request(request):
            if "api.empathy.co" in request.url:
                requests.append({"kind": "request", "method": request.method, "url": request.url})

        def on_response(response):
            if "api.empathy.co" in response.url:
                requests.append({"kind": "response", "status": response.status, "url": response.url})

        def on_failed(request):
            if "api.empathy.co" in request.url:
                requests.append({"kind": "failed", "failure": request.failure, "url": request.url})

        page.on("request", on_request)
        page.on("response", on_response)
        page.on("requestfailed", on_failed)
        page.goto(URL, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(25_000)
        print("EMPATHY_NETWORK_BEGIN")
        print(json.dumps(requests, ensure_ascii=False, indent=2))
        print("EMPATHY_NETWORK_END")
        context.close()
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
