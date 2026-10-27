"""Playwright isolated online-catalogue pilot for Farmacias YZA.

Location is neither required nor verified. Never claim a store, city-specific
price, postal code or regional availability from a visible online product.
"""
from __future__ import annotations

from contextlib import contextmanager
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

HOST = "www.yza.mx"
SKU_RE = re.compile(r"/MXYZ_([A-Za-z0-9_-]+)\.html$", re.I)
MONEY_RE = re.compile(r"\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)")
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
    """Legacy URL formatter only; YZA capture uses numbered UI buttons.

    Do not use ?p=N for real pagination: YZA may show page 1 again.
    """
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


def promotion_from_card(card: dict, current: float | None,
                        regular: float | None) -> str | None:
    """Store price-off evidence separately from visible promotional badges.

    A 'Compra 3 y llévate +1' badge is explicitly store-only in the
    operator's screenshot. Never infer eligibility for an online purchase.
    """
    notes = []
    text = card.get("text") or ""
    if ((current is not None and regular is not None and regular > current)
            or re.search(r"\boferta\b", text, re.I)):
        notes.append("Oferta")
    for badge in card.get("promo_badges") or []:
        label = str(badge).strip()
        if re.search(r"ribbons_plan_lealtad3", label, re.I):
            notes.append("Compra 3 y lleva 1 gratis (válido en sucursal)")
        elif re.search(r"2DO\s+AL\s+50%", label, re.I):
            notes.append("2do al 50% (etiqueta promocional)")
    return " | ".join(dict.fromkeys(notes)) or None


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
            if (title && (!byUrl.has(url) ||
                text.length > byUrl.get(url).text.length ||
                (text.length === byUrl.get(url).text.length &&
                 title.length > byUrl.get(url).title.length)))
              byUrl.set(url, {
                url, title, text,
                promo_badges: [...new Set(
                  [...box.querySelectorAll('img[alt],img[title]')]
                    .flatMap(img => [img.alt, img.title])
                    .map(x => (x || '').trim())
                    .filter(x => /ribbons_plan_lealtad3|2DO\\s+AL\\s+50%/i.test(x))
                )].slice(0, 5),
              });
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



@contextmanager
def yza_browser_page(playwright, cdp_url: str = ""):
    """Prefer Playwright-owned Chrome. Opt into an existing Chrome via CDP.

    Never close the operator's attached browser or existing tabs. A browser
    launched by this pilot is closed on exit, even on postal errors.
    """
    if cdp_url:
        # Chrome inspect may ask for operator approval before the browser
        # websocket becomes usable; allow time for the visible Allow dialog.
        browser = playwright.chromium.connect_over_cdp(
            cdp_url, timeout=60000, no_defaults=True
        )
        if not browser.contexts:
            raise YzaContextError("Chrome CDP no expone contextos de navegador.")
        context = browser.contexts[0]
        matching = [
            page for page in context.pages
            if urlparse(page.url).netloc.lower() in {HOST, "yza.mx"}
        ]
        if not matching:
            raise YzaContextError(
                "No hay pestaña de www.yza.mx en el Chrome conectado. "
                "Abre una pestaña de YZA en ese Chrome antes del piloto."
            )
        # Same procedure as San Pablo: never create a replacement page,
        # silently switch stores or close the user's selected tab.
        yield matching[-1]
        return

    browser = playwright.chromium.launch(headless=False, channel="chrome")
    try:
        context = browser.new_context(
            locale="es-MX", viewport={"width": 1440, "height": 1000},
        )
        try:
            page = context.new_page()
            yield page
        finally:
            context.close()
    finally:
        browser.close()



def open_online_category(page, category_url: str) -> None:
    """Navigate only an already-open YZA tab; no location read/write.

    A visible postal modal must be dismissed by the operator if it blocks
    product cards. This helper never fills location inputs or clicks them.
    """
    if urlparse(page.url).netloc.lower() not in {HOST, "yza.mx"}:
        raise YzaContextError("El Chrome conectado no tiene una pestaña YZA.")
    target = urlparse(category_url)
    if target.scheme != "https" or target.netloc != HOST:
        raise YzaScrapeError("URL de categoría YZA fuera del dominio permitido.")
    source = urlparse(page.url)
    if (source.path.rstrip("/") != target.path.rstrip("/")
            or source.query != target.query):
        page.goto(category_url, wait_until="domcontentloaded", timeout=75000)
        page.wait_for_timeout(900)



def click_catalogue_page(page, target_page: int, previous_cards: list[dict]) -> None:
    """Advance YZA's JavaScript BUTTON pagination and verify new products.

    The storefront uses buttons (1, 2, 3, 4, ..., last), not links.
    Navigating to ?p=2 can leave page 1 displayed even with HTTP success.
    Never silently count a repeated page as a completed category.
    """
    if target_page < 2:
        raise ValueError("La navegación por botones empieza desde la página 2.")
    before = sorted({str(card.get("url") or "").split("?")[0]
                     for card in previous_cards if card.get("url")})
    if not before:
        raise YzaScrapeError(
            f"No hay huellas de la página anterior para avanzar a {target_page}."
        )

    # Only use the real, visible numbered pager BUTTON, never an invented URL
    # or the ellipsis, which could skip products.
    candidates = page.get_by_role("button", name=str(target_page), exact=True)
    visible = []
    for i in range(min(candidates.count(), 30)):
        button = candidates.nth(i)
        if button.is_visible():
            visible.append(button)
    if len(visible) != 1:
        raise YzaScrapeError(
            f"No se encontró un botón único y visible para página "
            f"{target_page} (coincidencias visibles={len(visible)}). "
            "No se saltan páginas ni se utiliza la elipsis."
        )

    try:
        visible[0].click(timeout=12000)
        # Clicking an SPA pager can leave old cards visible for a while.
        # Wait for a NEW product URL in the rendered grid before reading.
        page.wait_for_function(
            """previous => {
              const old = new Set(previous);
              const links = [...document.querySelectorAll('a[href*="MXYZ_"]')]
                .filter(a => a.getClientRects().length)
                .map(a => a.href.split('?')[0]);
              return links.some(url => !old.has(url));
            }""",
            arg=before, timeout=25000, polling=250,
        )
        page.wait_for_timeout(350)
    except Exception as exc:
        raise YzaScrapeError(
            f"YZA no mostró productos nuevos tras hacer clic en página "
            f"{target_page}: {type(exc).__name__}. "
            "La paginación no está certificada."
        ) from exc




class YzaScraper:
    def __init__(self, cdp_url: str = "", max_pages: int = 100):
        self.cdp_url = cdp_url
        self.max_pages = max_pages
        self.run_meta: dict = {}

    def scrape_category(self, category, location) -> list[dict]:
        from playwright.sync_api import sync_playwright

        rows: list[dict] = []
        pages: list[dict] = []
        seen: set[str] = set()
        started = datetime.now().astimezone().isoformat(timespec="seconds")
        self.run_meta = {
            "status": "PARTIAL", "context_verified": False,
            "postal_verification_required": False,
            "scope": "ONLINE_CATALOGUE_NO_LOCATION_VERIFICATION",
            "pages": pages, "capture_date": started[:10],
        }
        with sync_playwright() as playwright:
            with yza_browser_page(playwright, self.cdp_url) as page:
                if self.cdp_url:
                    open_online_category(page, category.url)
                else:
                    page.goto(category.url, wait_until="domcontentloaded", timeout=75000)
                    page.wait_for_timeout(800)
                expected_last = None

                previous_cards: list[dict] = []
                for number in range(1, self.max_pages + 1):
                    if number == 1:
                        # Category was already opened; keep the real first-page
                        # state rather than a query-param reconstruction.
                        page.wait_for_timeout(650)
                    else:
                        click_catalogue_page(page, number, previous_cards)
                    if urlparse(page.url).netloc.lower() not in {HOST, "yza.mx"}:
                        raise YzaScrapeError("Redirección fuera de YZA")
                    body = page.locator("body").inner_text(timeout=10000)
                    if any(marker in body.lower() for marker in BLOCKED_MARKERS):
                        raise YzaScrapeError("YZA presentó bloqueo o verificación.")
                    cards, last = discover_cards(page)
                    previous_cards = cards
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
                        if urlparse(url).netloc.lower() not in {HOST, "yza.mx"}:
                            continue
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
                            "postal_code": None,
                            "store": None, "store_id": None,
                            "department": category.department,
                            "category": category.name,
                            "subcategory": category.subcategory,
                            "sub_subcategory": category.sub_subcategory,
                            "category_id": category.id,
                            "sku": sku, "brand": None,
                            "product": card["title"],
                            "price_current": current, "price_regular": regular,
                            "promotion": promotion_from_card(card, current, regular),
                            "availability_status": "UNKNOWN",
                            "is_available": None, "availability_raw": None,
                            "store_context_verified": False,
                            "store_context_method": "online_catalogue_no_location_verification",
                            "url": url, "price_raw": card["text"][:300],
                        })
                    if number > 1 and fresh == 0:
                        raise YzaScrapeError(
                            f"La página {number} repite productos o carece "
                            "de precios/SKU válidos. Paginación no verificada."
                        )
                    print(
                        f"YZA | {category.id} | pagina {number}/"
                        f"{expected_last} | tarjetas={len(cards)} | "
                        f"nuevos_validos={fresh}", flush=True,
                    )
                    pages.append({
                        "page": number, "cards": len(cards),
                        "new_valid": fresh, "last_page": expected_last,
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
        return rows
