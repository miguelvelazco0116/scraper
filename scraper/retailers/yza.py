"""Playwright pilot for Farmacias YZA (fixed postal code 11590).

Store-context verification is fail-closed. Never attribute category prices to
postal code 11590 merely because the input was filled or a profile exists.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

POSTAL_CODE = "11590"
HOST = "www.yza.mx"
SKU_RE = re.compile(r"/MXYZ_([A-Za-z0-9_-]+)\.html$", re.I)
MONEY_RE = re.compile(r"\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)")
CP_RE = re.compile(r"(?<!\d)11590(?!\d)")
BLOCKED_MARKERS = (
    "access denied", "request rejected", "captcha", "verify you are human",
    "too many requests", "acceso denegado",
)


class YzaContextError(RuntimeError):
    pass


class YzaScrapeError(RuntimeError):
    pass


def product_sku(url: str) -> str | None:
    match = SKU_RE.search(urlparse(url).path)
    return match.group(1) if match else None


def page_url(url: str, number: int) -> str:
    if number < 1:
        raise ValueError("La página debe ser positiva")
    parsed = urlparse(url)
    if parsed.netloc != HOST or parsed.scheme != "https":
        raise ValueError("URL fuera de Farmacias YZA")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if number == 1:
        query.pop("p", None)
    else:
        query["p"] = [str(number)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def prices_from_card(text: str) -> tuple[float | None, float | None]:
    values = [float(v.replace(",", "")) for v in MONEY_RE.findall(text or "")]
    values = [v for v in values if v > 0]
    if not values:
        return None, None
    current = values[0]
    # YZA commonly displays the offer first and the previous price second.
    regular = values[1] if len(values) > 1 and values[1] >= current else current
    return current, regular


def context_evidence(page) -> str | None:
    """Visible, persisted location text outside editable fields and popup.

    An input value, autocomplete option, HTML source or storage cookie is not
    sufficient evidence that the YZA storefront has applied this postal code.
    """
    return page.evaluate(
        """() => {
          const selectors = [
            'header [class*="address"]', 'header [class*="location"]',
            'header [class*="postal"]', 'header [class*="zip"]',
            '[class*="selected-address"]', '[id*="selected-address"]',
            '[class*="current-address"]', '[class*="current-location"]',
            '[data-role="selected-address"]', '[data-role="location"]'
          ];
          const target = /(^|\\D)11590(\\D|$)/;
          for (const selector of selectors) {
            for (const el of document.querySelectorAll(selector)) {
              if (!el.getClientRects().length ||
                  el.closest('[role="dialog"], .modal-popup, .pac-container')) continue;
              const text = (el.innerText || el.textContent || '').trim();
              if (text.length && text.length < 250 && target.test(text)) {
                return selector + ': ' + text;
              }
            }
          }
          return null;
        }"""
    )


def apply_postal_code(page) -> str | None:
    proof = context_evidence(page)
    if proof:
        return proof
    # Only select a suggestion that explicitly mentions CP 11590.
    field = page.get_by_placeholder(
        "Digita la dirección y selecciona una opción", exact=False
    )
    try:
        if field.count() and field.first.is_visible():
            field.first.fill(POSTAL_CODE)
            page.wait_for_timeout(1100)
            for selector in (
                ".pac-item", "[role='option']", ".autocomplete-suggestion",
                ".ui-menu-item", "[class*='address-suggestion']",
            ):
                choices = page.locator(selector).filter(has_text=POSTAL_CODE)
                if choices.count() and choices.first.is_visible():
                    choices.first.click(timeout=3500)
                    page.wait_for_timeout(900)
                    return context_evidence(page)
    except Exception:
        # UI differences must not turn an unverified address into success.
        pass
    return None


def discover_cards(page) -> tuple[list[dict], int | None]:
    """Discover visible product links, price text and pager without guessing totals."""
    return_value = page.evaluate(
        """() => {
          const candidates = [...document.querySelectorAll('a[href*="MXYZ_"]')]
            .filter(a => /\\/MXYZ_[A-Za-z0-9_-]+\\.html(?:[?#]|$)/i.test(a.href));
          const byUrl = new Map();
          for (const a of candidates) {
            if (!a.getClientRects().length) continue;
            const url = a.href.split('?')[0];
            let box = a;
            for (let level=0; level<7 && box.parentElement; level++) {
              const ancestor = box.parentElement;
              const links = ancestor.querySelectorAll('a[href*="MXYZ_"]');
              box = ancestor;
              if ((box.innerText || '').includes('$') && links.length <= 4) break;
              if (links.length > 5) break;
            }
            const text = (box.innerText || '').replace(/\\s+/g,' ').trim();
            let title = (a.innerText || a.querySelector('img')?.alt || '').trim();
            if (!title) {
              const other = [...box.querySelectorAll('a[href]')]
                .find(x => x.href.split('?')[0] === url && (x.innerText || '').trim());
              title = (other?.innerText || '').trim();
            }
            if (title && (!byUrl.has(url) || text.length > byUrl.get(url).text.length))
              byUrl.set(url, {url, title, text});
          }
          const pages = [...document.querySelectorAll('a[href], button, [data-page]')]
            .map(el => {
              const match = (el.getAttribute('href') || '').match(/[?&]p=(\\d+)/);
              const num = match?.[1] || el.getAttribute('data-page') ||
                (el.closest('.pages, .pagination') ? (el.innerText || '').trim() : '');
              return /^\\d+$/.test(num) ? Number(num) : 0;
            }).filter(n => n > 0 && n <= 1000);
          return {cards:[...byUrl.values()], lastPage: pages.length ? Math.max(...pages) : null};
        }"""
    )
    return return_value["cards"], return_value["lastPage"]


class YzaScraper:
    def __init__(self, cdp_url: str, max_pages: int = 100):
        self.cdp_url = cdp_url
        self.max_pages = max_pages
        self.run_meta: dict = {}

    def scrape_category(self, category, location) -> list[dict]:
        if location.postal_code != POSTAL_CODE:
            raise YzaContextError("YZA solo permite CP 11590")
        from playwright.sync_api import sync_playwright

        rows: list[dict] = []
        pages: list[dict] = []
        seen: set[str] = set()
        started = datetime.now().astimezone().isoformat(timespec="seconds")
        self.run_meta = {
            "status": "PARTIAL", "context_verified": False,
            "pages": pages, "capture_date": started[:10],
        }
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(self.cdp_url)
            context = browser.contexts[0]
            tabs = [
                p for p in context.pages
                if urlparse(p.url).netloc.lower() in {HOST, "yza.mx"}
            ]
            if not tabs:
                raise YzaContextError(
                    "Abre una pestaña de www.yza.mx en el Chrome con CDP, "
                    "selecciona una dirección con CP 11590 y reintenta."
                )
            page = tabs[0]
            page.goto(category.url, wait_until="domcontentloaded", timeout=75000)
            page.wait_for_timeout(800)
            evidence = apply_postal_code(page)
            if not evidence:
                raise YzaContextError(
                    "CP 11590 NO confirmado en el selector persistente de YZA. "
                    "Selecciona una dirección sugerida con ese CP en Chrome. "
                    "No se atribuyen precios a una ubicación no verificada."
                )
            self.run_meta["context_verified"] = True
            self.run_meta["context_evidence"] = evidence
            expected_last = None

            for number in range(1, self.max_pages + 1):
                page.goto(page_url(category.url, number),
                          wait_until="domcontentloaded", timeout=75000)
                page.wait_for_timeout(700)
                proof = context_evidence(page)
                if not proof:
                    raise YzaContextError(
                        f"Se perdió el CP 11590 en página {number}; captura no válida"
                    )
                body = page.locator("body").inner_text(timeout=10000)
                if any(marker in body.lower() for marker in BLOCKED_MARKERS):
                    raise YzaScrapeError("YZA presentó bloqueo o verificación.")
                cards, last = discover_cards(page)
                if number == 1:
                    expected_last = last
                    if expected_last is None:
                        raise YzaScrapeError(
                            "No se pudo certificar el número de páginas del catálogo."
                        )
                    if expected_last > self.max_pages:
                        raise YzaScrapeError(
                            f"Catálogo requiere {expected_last} páginas; "
                            f"max-pages={self.max_pages}"
                        )
                if not cards:
                    raise YzaScrapeError(f"Sin productos visibles en página {number}.")
                fresh = 0
                for card in cards:
                    url = urljoin(category.url, card["url"])
                    if url in seen:
                        continue
                    seen.add(url)
                    sku = product_sku(url)
                    current, regular = prices_from_card(card["text"])
                    if not sku or not current:
                        continue
                    fresh += 1
                    rows.append({
                        "scrape_timestamp": started,
                        "retailer": "Farmacias YZA",
                        "city": location.city, "state": location.state,
                        "postal_code": POSTAL_CODE,
                        "store": None, "store_id": None,
                        "department": category.department,
                        "category": category.name,
                        "subcategory": category.subcategory,
                        "sub_subcategory": category.sub_subcategory,
                        "category_id": category.id,
                        "sku": sku, "brand": None,
                        "product": card["title"],
                        "price_current": current, "price_regular": regular,
                        "promotion": "Oferta" if regular and regular > current else None,
                        "availability_status": "UNKNOWN",
                        "is_available": None, "availability_raw": None,
                        "store_context_verified": True,
                        "store_context_method": "yza_visible_selected_address_cp11590",
                        "url": url, "price_raw": card["text"][:300],
                    })
                pages.append({
                    "page": number, "cards": len(cards),
                    "new_valid": fresh, "last_page": expected_last,
                    "context_evidence": proof,
                })
                if number >= expected_last:
                    break
            count = sum(p["cards"] for p in pages)
            unique_links = len(seen)
            self.run_meta.update({
                "target_products": unique_links,
                "raw_cards": count,
                "products": len(rows),
                "pages_expected": expected_last,
                "pages_visited": len(pages),
                "discovery_complete": bool(
                    expected_last and len(pages) == expected_last
                    and len(rows) == unique_links and unique_links > 0
                ),
            })
            if self.run_meta["discovery_complete"]:
                self.run_meta["status"] = "SUCCESS"
            # CDP browser belongs to the operator; do not close Chrome.
        return rows
