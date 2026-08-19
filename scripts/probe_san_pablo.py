from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

TARGETS = {
    "descongestionantes": "https://www.farmaciasanpablo.com.mx/medicamentos/gripe-y-tos/descongestionantes/",
    "preservativos": "https://www.farmaciasanpablo.com.mx/salud-sexual/bienestar-sexual/preservativos/",
    "enjuagues-bucales": "https://www.farmaciasanpablo.com.mx/cuidado-personal-y-belleza/cuidado-bucal/enjuagues-bucales/c/030040003",
    "pastas-dentales": "https://www.farmaciasanpablo.com.mx/cuidado-personal-y-belleza/cuidado-bucal/pastas-dentales/",
}


def main() -> int:
    Path("diagnostics").mkdir(exist_ok=True)
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="es-MX", viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        for key, url in TARGETS.items():
            item = {"id": key, "requested": url}
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                page.wait_for_timeout(3_000)
                item.update({
                    "status": response.status if response else None,
                    "final_url": page.url,
                    "title": page.title(),
                    "product_links": page.locator('a[href*="/p/"]').count(),
                    "category_links": page.locator('a[href*="/c/"]').count(),
                    "body_excerpt": (page.locator("body").inner_text(timeout=10_000) or "")[:1500],
                })
                links = page.locator('a[href*="/c/"]').evaluate_all(
                    "els => els.map(a => ({text:(a.innerText||'').trim(), href:a.href})).filter(x => x.text || x.href).slice(0,500)"
                )
                item["matching_category_links"] = [
                    x for x in links
                    if any(term in ((x.get("text") or "") + " " + (x.get("href") or "")).lower()
                           for term in ("descongestion", "preserv", "enjuague", "pasta-dental", "pastas-dentales"))
                ][:50]
                Path(f"diagnostics/san_pablo_probe_{key}.html").write_text(page.content(), encoding="utf-8")
                page.screenshot(path=f"diagnostics/san_pablo_probe_{key}.png", full_page=True)
            except Exception as exc:
                item["error"] = f"{type(exc).__name__}: {exc}"
            results.append(item)
            print(json.dumps(item, ensure_ascii=False))
        context.close()
        browser.close()
    Path("diagnostics/san_pablo_probe.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
