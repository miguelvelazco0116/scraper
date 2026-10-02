from __future__ import annotations

import argparse
import json
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


URLS = {
    "descongestionantes": "https://www.farmaciasanpablo.com.mx/medicamentos/gripe-y-tos/descongestionantes/c/060070004",
    "preservativos": "https://www.farmaciasanpablo.com.mx/salud-sexual/bienestar-sexual/preservativos/",
    "enjuagues-bucales": "https://www.farmaciasanpablo.com.mx/cuidado-personal-y-belleza/cuidado-bucal/enjuagues-bucales/c/030040003",
    "pastas-dentales": "https://www.farmaciasanpablo.com.mx/salud-natural/salud-natural/tes/c/Pastas-dentales0/c/030040007",
}

DIAGNOSTICS = Path("diagnostics")


def blocked(text: str, title: str) -> bool:
    blob = f"{title}\n{text}".casefold()
    markers = [
        "access denied",
        "you don't have permission to access",
        "request rejected",
        "verify you are human",
        "verifica que eres humano",
        "captcha",
    ]
    return any(marker in blob for marker in markers)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", choices=sorted(URLS), default="enjuagues-bucales")
    args = parser.parse_args()

    options = webdriver.ChromeOptions()
    options.add_argument("--lang=es-MX")
    options.add_argument("--window-size=1440,1000")

    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(60)

    DIAGNOSTICS.mkdir(parents=True, exist_ok=True)
    out = DIAGNOSTICS / f"san_pablo_{args.category}_chrome_test.json"

    try:
        url = URLS[args.category]
        print(f"Abriendo Chrome: {url}")

        try:
            driver.get(url)
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
        except (TimeoutException, WebDriverException) as exc:
            payload = {
                "category": args.category,
                "url": url,
                "status": "NETWORK_OR_BROWSER_ERROR",
                "error": f"{type(exc).__name__}: {exc}",
            }
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 3

        title = driver.title or ""
        body = driver.find_element(By.TAG_NAME, "body").text or ""
        is_blocked = blocked(body, title)

        structure = driver.execute_script(
            r"""
            const anchors = Array.from(document.querySelectorAll('a[href]'));
            const hrefs = [...new Set(anchors.map(a => a.href).filter(Boolean))];

            const dataNodes = Array.from(document.querySelectorAll(
              '[data-product-code], [data-product-id], [data-code], [data-sku], [data-ean], [data-upc]'
            ));

            const textNodes = Array.from(document.querySelectorAll('body *'))
              .filter(el => /Listerine|Enjuague bucal/i.test((el.innerText || '').trim()))
              .slice(0, 8)
              .map(el => {
                let node = el;
                for (let i = 0; i < 6 && node; i++, node = node.parentElement) {
                  const text = (node.innerText || '').trim();
                  if (/\$\s*\d/.test(text) && text.length < 2500) break;
                }
                node = node || el;
                return {
                  tag: node.tagName,
                  id: node.id || '',
                  className: String(node.className || ''),
                  href: node.matches('a[href]') ? node.href : '',
                  dataProductCode: node.getAttribute('data-product-code') || '',
                  dataProductId: node.getAttribute('data-product-id') || '',
                  dataCode: node.getAttribute('data-code') || '',
                  text: (node.innerText || '').trim().slice(0, 800),
                  html: node.outerHTML.slice(0, 1800)
                };
              });

            return {
              totalAnchors: anchors.length,
              sampleHrefs: hrefs.slice(0, 80),
              dataNodes: dataNodes.slice(0, 30).map(el => ({
                tag: el.tagName,
                id: el.id || '',
                className: String(el.className || ''),
                productCode: el.getAttribute('data-product-code') || '',
                productId: el.getAttribute('data-product-id') || '',
                code: el.getAttribute('data-code') || '',
                sku: el.getAttribute('data-sku') || '',
                ean: el.getAttribute('data-ean') || '',
                upc: el.getAttribute('data-upc') || '',
                text: (el.innerText || '').trim().slice(0, 500)
              })),
              textNodes
            };
            """
        ) or {}
        product_links = sum(
            1 for href in structure.get("sampleHrefs", [])
            if "/p/" in href
        )

        payload = {
            "category": args.category,
            "url": url,
            "title": title,
            "blocked": is_blocked,
            "product_links": int(product_links),
            "total_anchors": int(structure.get("totalAnchors") or 0),
            "sample_hrefs": structure.get("sampleHrefs") or [],
            "data_nodes": structure.get("dataNodes") or [],
            "candidate_product_nodes": structure.get("textNodes") or [],
            "body_preview": " ".join(body.split())[:500],
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        try:
            driver.save_screenshot(
                str(DIAGNOSTICS / f"san_pablo_{args.category}_chrome_test.png")
            )
        except Exception:
            pass

        print(json.dumps(payload, ensure_ascii=False, indent=2))

        if is_blocked:
            print("RESULTADO: BLOCKED")
            return 2

        if int(product_links) == 0:
            print("RESULTADO: ACCESO OK; estructura de productos guardada para identificar selectores")
            return 4

        print("RESULTADO: ACCESO OK")
        return 0
    finally:
        driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
