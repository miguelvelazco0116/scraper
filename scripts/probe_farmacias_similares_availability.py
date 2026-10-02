from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from scraper.config import load_categories


OUT_DIR = ROOT / "diagnostics"


def interesting_url(url: str) -> bool:
    folded = (url or "").casefold()
    markers = (
        "/api/",
        "catalog",
        "search",
        "graphql",
        "product",
        "vtex",
        "intelligent-search",
    )
    return any(marker in folded for marker in markers)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe de red/DOM para Farmacias Similares"
    )
    parser.add_argument("--category", default="aparato-respiratorio")
    parser.add_argument("--pages", type=int, default=4)
    args = parser.parse_args()

    categories = {
        x.id: x
        for x in load_categories(
            ROOT / "config" / "farmacias-similares" / "categories.yaml"
        )
    }
    category = categories.get(args.category)
    if category is None:
        raise SystemExit(f"Categoría no encontrada: {args.category}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    captured: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, channel="chrome")
        context = browser.new_context(
            locale="es-MX",
            viewport={"width": 1440, "height": 1000},
        )
        page = context.new_page()

        def on_response(response):
            url = response.url
            if not interesting_url(url):
                return
            record = {
                "url": url,
                "status": response.status,
                "content_type": response.headers.get("content-type"),
            }
            try:
                ctype = (record["content_type"] or "").casefold()
                if "json" in ctype:
                    payload = response.json()
                    raw = json.dumps(payload, ensure_ascii=False)
                    record["body_preview"] = raw[:12000]
                    record["body_length"] = len(raw)
                else:
                    text = response.text()
                    if re.search(
                        r"availableQuantity|availability|stock|productId|seller|commertialOffer|items",
                        text,
                        flags=re.IGNORECASE,
                    ):
                        record["body_preview"] = text[:12000]
                        record["body_length"] = len(text)
            except Exception as exc:
                record["read_error"] = f"{type(exc).__name__}: {exc}"
            captured.append(record)

        page.on("response", on_response)

        page_results = []
        base = category.url
        for page_number in range(1, max(1, args.pages) + 1):
            sep = "&" if "?" in base else "?"
            url = base if page_number == 1 else f"{base}{sep}page={page_number}"
            print(f"Abriendo página {page_number}: {url}", flush=True)
            page.goto(url, wait_until="networkidle", timeout=120_000)
            page.wait_for_timeout(2500)

            dom = page.locator("body").evaluate(
                r"""
                () => {
                  const norm = value =>
                    String(value || '').replace(/\s+/g, ' ').trim();
                  const body = norm(document.body.innerText || document.body.textContent);
                  const links = Array.from(document.querySelectorAll('a[href]'))
                    .map(a => a.href)
                    .filter(href => /\/p\/?(?:\?|$)/i.test(href));
                  const unavailable = Array.from(
                    document.querySelectorAll('body *')
                  ).map(el => norm(el.innerText || el.textContent))
                   .filter(text =>
                     text &&
                     text.length <= 500 &&
                     /Agotado|No disponible|Sin existencia|Sin stock|Temporalmente no disponible/i.test(text)
                   );
                  const scripts = Array.from(document.scripts)
                    .map(s => s.textContent || '')
                    .filter(text =>
                      /availableQuantity|availability|stock|productId|commertialOffer|items/i.test(text)
                    )
                    .map(text => text.slice(0, 12000));
                  return {
                    title: document.title,
                    body_preview: body.slice(0, 5000),
                    product_links: [...new Set(links)],
                    unavailable_texts: [...new Set(unavailable)].slice(0, 100),
                    interesting_scripts: scripts.slice(0, 20),
                  };
                }
                """
            )

            html_path = OUT_DIR / (
                f"farmacias_similares_{args.category}_page_{page_number}.html"
            )
            html_path.write_text(page.content(), encoding="utf-8")

            page_results.append(
                {
                    "page": page_number,
                    "url": page.url,
                    **dom,
                }
            )

        performance_urls = page.evaluate(
            """
            () => performance.getEntriesByType('resource')
              .map(entry => entry.name || '')
              .filter(Boolean)
            """
        )

        context.close()
        browser.close()

    result = {
        "category_id": category.id,
        "category_url": category.url,
        "pages": page_results,
        "network_candidates": captured,
        "performance_urls": [
            url for url in performance_urls if interesting_url(url)
        ],
    }

    out_path = OUT_DIR / (
        f"farmacias_similares_{args.category}_availability_probe.json"
    )
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("")
    print("RESUMEN")
    for item in page_results:
        print(
            f"page={item['page']} "
            f"links={len(item.get('product_links') or [])} "
            f"unavailable_texts={len(item.get('unavailable_texts') or [])} "
            f"scripts={len(item.get('interesting_scripts') or [])}"
        )
    print(f"network_candidates={len(captured)}")
    print(f"Diagnóstico: {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
