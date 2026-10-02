from __future__ import annotations

import argparse
import json
import time
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

        # Hydrate lazy/virtual catalogue content before inspecting the DOM.
        for _ in range(24):
            driver.execute_script(
                "window.scrollBy(0, Math.max(500, Math.floor(window.innerHeight * 0.8)));"
            )
            time.sleep(0.20)
        time.sleep(0.8)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.3)

        structure = driver.execute_script(
            r"""
            const normalize = value => String(value || '').replace(/\s+/g, ' ').trim();
            const moneyRe = /\$\s*[0-9][0-9,]*(?:\.\d{1,2})?/;

            function attrs(el) {
              if (!el || !el.attributes) return {};
              const out = {};
              for (const a of Array.from(el.attributes)) {
                if (
                  /^(data-|href$|id$|class$|role$|aria-|name$|value$|title$)/i.test(a.name)
                ) {
                  out[a.name] = a.value;
                }
              }
              return out;
            }

            function ancestorSamples(seed) {
              const result = [];
              let node = seed;
              for (let depth = 0; depth < 9 && node; depth++, node = node.parentElement) {
                const raw = (node.innerText || node.textContent || '').trim();
                const text = normalize(raw);
                const anchors = Array.from(node.querySelectorAll('a[href]'))
                  .slice(0, 10)
                  .map(a => ({
                    href: a.href || a.getAttribute('href') || '',
                    text: normalize(a.innerText || a.textContent),
                    attrs: attrs(a)
                  }));
                result.push({
                  depth,
                  tag: node.tagName,
                  attrs: attrs(node),
                  text: text.slice(0, 900),
                  hasMoney: moneyRe.test(text),
                  anchors,
                  html: (node.outerHTML || '').slice(0, 4500)
                });
              }
              return result;
            }

            const occMeta = document.querySelector('meta[name="occ-backend-base-url"]');
            const occBackendBaseUrl = occMeta ? (occMeta.getAttribute('content') || '') : '';

            const resourceUrls = Array.from(performance.getEntriesByType('resource'))
              .map(entry => entry.name || '')
              .filter(Boolean);

            const apiCandidates = resourceUrls.filter(url =>
              /occ|products?\/search|\/products?\?|category|catalog/i.test(url)
            );

            const anchors = Array.from(document.querySelectorAll('a[href]'));
            const hrefs = [...new Set(anchors.map(a => a.href || a.getAttribute('href')).filter(Boolean))];
            const productHrefs = hrefs.filter(h => /\/p\/\d+(?:[/?#]|$)/i.test(h));

            const addControls = Array.from(document.querySelectorAll(
              'button, a, [role="button"], input[type="button"], input[type="submit"]'
            )).filter(el =>
              /Agregar|Añadir/i.test(normalize(el.innerText || el.textContent || el.value))
            );

            const addSamples = addControls.slice(0, 12).map(el => ({
              tag: el.tagName,
              text: normalize(el.innerText || el.textContent || el.value),
              attrs: attrs(el),
              ancestors: ancestorSamples(el)
            }));

            const dataElements = Array.from(document.querySelectorAll('*'))
              .filter(el => Array.from(el.attributes || []).some(a =>
                /product|sku|ean|upc|item|code|article|url/i.test(a.name + '=' + a.value)
              ))
              .slice(0, 120)
              .map(el => ({
                tag: el.tagName,
                attrs: attrs(el),
                text: normalize(el.innerText || el.textContent).slice(0, 500),
                html: (el.outerHTML || '').slice(0, 2500)
              }));

            const moneyLeaves = Array.from(document.querySelectorAll('body *'))
              .filter(el => {
                const text = normalize(el.innerText || el.textContent);
                if (!text || text.length > 220 || !moneyRe.test(text)) return false;
                return !Array.from(el.children || []).some(child =>
                  moneyRe.test(normalize(child.innerText || child.textContent))
                );
              })
              .slice(0, 30)
              .map(el => ({
                tag: el.tagName,
                attrs: attrs(el),
                text: normalize(el.innerText || el.textContent),
                ancestors: ancestorSamples(el).slice(0, 5)
              }));

            const scripts = Array.from(document.querySelectorAll('script'))
              .filter(s => {
                const text = s.textContent || '';
                return /product|sku|ean|gtin|price/i.test(text) && text.length > 20;
              })
              .slice(0, 20)
              .map(s => ({
                type: s.type || '',
                id: s.id || '',
                text: (s.textContent || '').slice(0, 7000)
              }));

            return {
              url: location.href,
              occBackendBaseUrl,
              apiCandidates: [...new Set(apiCandidates)].slice(0, 120),
              totalAnchors: anchors.length,
              totalUniqueHrefs: hrefs.length,
              productHrefs,
              hrefSamples: hrefs.slice(0, 200),
              addControlCount: addControls.length,
              addSamples,
              dataElements,
              moneyLeaves,
              scripts
            };
            """
        ) or {}
        product_hrefs = structure.get("productHrefs") or []
        product_links = len(product_hrefs)

        payload = {
            "category": args.category,
            "url": url,
            "title": title,
            "blocked": is_blocked,
            "occ_backend_base_url": structure.get("occBackendBaseUrl") or "",
            "api_candidates": structure.get("apiCandidates") or [],
            "product_links": int(product_links),
            "product_hrefs": product_hrefs,
            "total_anchors": int(structure.get("totalAnchors") or 0),
            "href_samples": structure.get("hrefSamples") or [],
            "add_control_count": int(structure.get("addControlCount") or 0),
            "add_samples": structure.get("addSamples") or [],
            "data_elements": structure.get("dataElements") or [],
            "money_leaves": structure.get("moneyLeaves") or [],
            "scripts": structure.get("scripts") or [],
            "body_preview": " ".join(body.split())[:500],
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        try:
            driver.save_screenshot(
                str(DIAGNOSTICS / f"san_pablo_{args.category}_chrome_test.png")
            )
        except Exception:
            pass

        print("")
        print("RESUMEN DOM")
        print(f"  blocked          : {is_blocked}")
        print(f"  occ_backend      : {payload['occ_backend_base_url'] or '-'}")
        print(f"  api_candidates   : {len(payload['api_candidates'])}")
        print(f"  anchors          : {payload['total_anchors']}")
        print(f"  product_links    : {payload['product_links']}")
        print(f"  add_controls     : {payload['add_control_count']}")
        print(f"  data_elements    : {len(payload['data_elements'])}")
        print(f"  money_leaves     : {len(payload['money_leaves'])}")
        print(f"  scripts_product  : {len(payload['scripts'])}")

        if payload["api_candidates"]:
            print("  sample_api_urls:")
            for value in payload["api_candidates"][:10]:
                print(f"    - {value}")

        if payload["product_hrefs"]:
            print("  sample_product_urls:")
            for value in payload["product_hrefs"][:5]:
                print(f"    - {value}")

        if payload["add_samples"]:
            sample = payload["add_samples"][0]
            print("  first_add_attrs:")
            print(json.dumps(sample.get("attrs") or {}, ensure_ascii=False))
            ancestors = sample.get("ancestors") or []
            for anc in ancestors[:5]:
                attrs = anc.get("attrs") or {}
                if attrs:
                    print(
                        f"    ancestor depth={anc.get('depth')} tag={anc.get('tag')} "
                        + json.dumps(attrs, ensure_ascii=False)
                    )

        if payload["data_elements"]:
            print("  sample_data_attributes:")
            for item in payload["data_elements"][:8]:
                print(
                    f"    {item.get('tag')} "
                    + json.dumps(item.get("attrs") or {}, ensure_ascii=False)
                )

        print(f"Diagnóstico completo: {out}")

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
