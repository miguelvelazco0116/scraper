"""Playwright pilot for Farmacias YZA (fixed postal code 11590).

Store-context verification is fail-closed. Never attribute category prices to
postal code 11590 merely because the input was filled or a profile exists.
"""
from __future__ import annotations

from contextlib import contextmanager
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
    """Confirm CP 11590 in a visible, *applied* delivery control.

    Excludes text from modals, inputs, hidden templates and product cards.
    Unlike the old generic address selector this also looks for YZA's
    delivery label ('Entregar productos en') in its persistent header.
    """
    return page.evaluate(
        r"""() => {
          const postal = /(^|\D)11590(\D|$)/;
          const visible = (el) => !!el.getClientRects().length
            && getComputedStyle(el).visibility !== 'hidden'
            && getComputedStyle(el).display !== 'none';
          const inDialog = (el) => !!el.closest(
            '[role="dialog"],dialog,[aria-modal="true"],.modal-popup,'
            + '.pac-container,[class*="modal" i],[class*="popup" i]'
          );
          const selectors = [
            'header [class*="address" i]',
            'header [class*="location" i]',
            'header [class*="postal" i]',
            'header [class*="zip" i]',
            'header [class*="deliver" i]',
            'header [class*="delivery" i]',
            'header [class*="entrega" i]',
            'header [class*="direcci" i]',
            '[class*="selected-address" i]',
            '[id*="selected-address" i]',
            '[class*="current-address" i]',
            '[class*="current-location" i]',
            '[data-role="selected-address"]',
            '[data-role="location"]'
          ];
          for (const selector of selectors) {
            for (const el of document.querySelectorAll(selector)) {
              if (!visible(el) || inDialog(el)) continue;
              const value = (el.innerText || '').replace(/\s+/g,' ').trim();
              if (value && value.length <= 250 && postal.test(value)) {
                return 'yza_delivery_postal_confirmed:11590';
              }
            }
          }
          // Header may have no semantic address class: require explicit
          // delivery-context wording adjacent to the exact postal code.
          const header = document.querySelector('header,[role="banner"]');
          if (header) {
            const nodes = header.querySelectorAll(
              'button,a,span,div,[role="button"]'
            );
            for (const el of nodes) {
              if (!visible(el) || inDialog(el)) continue;
              if (el.closest('input,textarea,select')) continue;
              const value = (el.innerText || '').replace(/\s+/g,' ').trim();
              if (!value || value.length > 150 || !postal.test(value)) continue;
              if (/entregar|entrega|env[ií]o|direcci[oó]n|postal|ubicaci[oó]n/i
                  .test(value)) {
                return 'yza_delivery_postal_confirmed:11590';
              }
              // Some header themes replace the location label with
              // the bare, applied five-digit CP. Accept it only in
              // the top-left delivery-control region, not elsewhere.
              if (value === '11590') {
                const rect = el.getBoundingClientRect();
                if (rect.top >= 0 && rect.top <= 180 &&
                    rect.left >= 0 && rect.left <= 600) {
                  return 'yza_header_postal_confirmed:11590';
                }
              }
            }
          }
          return null;
        }"""
    )


def _first_visible(locator, limit: int = 60):
    """Prefer an actual visible widget over hidden duplicates of modal forms."""
    try:
        for index in range(min(locator.count(), limit)):
            candidate = locator.nth(index)
            if candidate.is_visible():
                return candidate
    except Exception:
        return None
    return None


def _postal_modal_controls(page):
    """YZA popup: 'Ingresa un código postal' / 'Código Postal' / ACEPTAR.

    Modal copies may exist in the DOM while hidden. Never fill or click them.
    """
    field = _first_visible(
        page.get_by_placeholder(re.compile(r"c[oó]digo\s*postal", re.I))
    )
    if field is None:
        return None, None
    button = _first_visible(
        page.get_by_role("button", name=re.compile(r"^\s*aceptar\s*$", re.I))
    )
    if button is None:
        button = _first_visible(
            page.get_by_text(re.compile(r"^\s*aceptar\s*$", re.I), exact=True)
        )
    return field, button



def _initial_postal_popup_visible(page) -> bool:
    """Detect first-load YZA popup by its actual title, not hidden DOM copies."""
    try:
        title = page.get_by_text(
            re.compile(r"^\s*ingresa\s+un\s+c[oó]digo\s+postal\s*$", re.I),
            exact=True,
        )
        return _first_visible(title) is not None
    except Exception:
        return False



def _open_postal_modal(page, trace: dict | None = None) -> bool:
    """Open header delivery chooser then, if necessary, its CP sub-dialog."""
    if trace is None:
        trace = {}
    for pattern in (
        r"^\s*ingresa\s+tu\s+direcci[oó]n\s*$",
        r"^\s*entregar\s+productos\s+en\s*$",
    ):
        controls = page.get_by_text(re.compile(pattern, re.I), exact=False)
        trigger = _first_visible(controls)
        if trigger is None:
            continue
        try:
            trace["delivery_header_clicked"] = True
            trigger.click(timeout=5000)
            page.wait_for_timeout(500)
            field, button = _postal_modal_controls(page)
            if field is not None and button is not None:
                trace["postal_form_found_after_header"] = True
                return True
        except Exception as exc:
            trace["header_click_error"] = type(exc).__name__

    # YZA has multiple address popups. "Ingresa un código postal" may be an
    # intermediate action after the first (generic) address dialog.
    for pattern in (
        r"^\s*ingresa\s+un\s+c[oó]digo\s+postal\s*$",
        r"^\s*actualizar\s+una\s+direcci[oó]n\s*$",
    ):
        controls = page.get_by_text(re.compile(pattern, re.I), exact=True)
        for index in range(min(controls.count(), 30)):
            try:
                control = controls.nth(index)
                if not control.is_visible():
                    continue
                trace["postal_subdialog_clicked"] = True
                control.click(timeout=5000)
                page.wait_for_timeout(450)
                field, button = _postal_modal_controls(page)
                if field is not None and button is not None:
                    trace["postal_form_found_after_subdialog"] = True
                    return True
            except Exception as exc:
                trace["subdialog_click_error"] = type(exc).__name__
    return False


def apply_postal_code(page, trace: dict | None = None) -> str | None:
    """Set CP 11590 using YZA's visible modal and certify persistent context.

    trace is optional non-sensitive debug metadata: no addresses, typed
    field values, tokens, storage or page HTML are recorded.
    """
    if trace is None:
        trace = {}
    trace["requested_cp"] = POSTAL_CODE
    trace["stage"] = "check_initial_header"
    proof = context_evidence(page)
    if proof:
        trace["stage"] = "already_confirmed"
        return proof

    # YZA starts with the postal popup already OPEN. This comes BEFORE any
    # header clicks or generic address chooser. A briefly hydrating popup
    # deserves a short wait, not a click behind the overlay.
    trace["stage"] = "locate_initial_postal_popup"
    popup_visible = _initial_postal_popup_visible(page)
    trace["initial_postal_popup_visible"] = popup_visible
    field, button = _postal_modal_controls(page)
    trace["postal_field_initially_visible"] = field is not None
    trace["accept_initially_visible"] = button is not None

    if popup_visible and (field is None or button is None):
        trace["stage"] = "wait_for_initial_popup_controls"
        for _ in range(6):
            page.wait_for_timeout(400)
            field, button = _postal_modal_controls(page)
            if field is not None and button is not None:
                break

    if field is None or button is None:
        if popup_visible:
            # Never click the header behind a visible postal modal. The
            # controls may have changed; fail safely with diagnostic trace.
            trace["stage"] = "initial_popup_visible_but_controls_missing"
            trace["postal_field_visible"] = field is not None
            trace["accept_visible"] = button is not None
            return None
        trace["stage"] = "open_postal_popup_from_header"
        _open_postal_modal(page, trace)
        field, button = _postal_modal_controls(page)

    trace["postal_field_visible"] = field is not None
    trace["accept_visible"] = button is not None
    if field is None or button is None:
        trace["stage"] = "postal_controls_not_found"
        return None

    try:
        trace["stage"] = "fill_postal"
        field.fill(POSTAL_CODE, timeout=5000)
        if field.input_value(timeout=2000).strip() != POSTAL_CODE:
            trace["stage"] = "postal_input_did_not_update"
            return None
        trace["field_filled"] = True
        trace["stage"] = "click_accept"
        button.click(timeout=7000)
        trace["accept_clicked"] = True
    except Exception as exc:
        trace["stage"] = "form_action_error"
        trace["form_action_error_type"] = type(exc).__name__
        return None

    trace["stage"] = "confirm_applied_postal_in_header"
    for attempt in range(16):
        page.wait_for_timeout(500)
        try:
            proof = context_evidence(page)
        except Exception as exc:
            trace["last_header_check_error_type"] = type(exc).__name__
            continue
        if proof:
            trace["stage"] = "applied_postal_confirmed"
            trace["header_check_attempt"] = attempt + 1
            return proof
    trace["stage"] = "accept_clicked_but_postal_not_confirmed"
    try:
        field_after, button_after = _postal_modal_controls(page)
        trace["postal_field_still_visible_after_accept"] = field_after is not None
        trace["accept_still_visible_after_accept"] = button_after is not None
    except Exception:
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
            if (title && (!byUrl.has(url) ||
                text.length > byUrl.get(url).text.length ||
                (text.length === byUrl.get(url).text.length &&
                 title.length > byUrl.get(url).title.length)))
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



@contextmanager
def yza_browser_page(playwright, cdp_url: str = ""):
    """Prefer Playwright-owned Chrome. Opt into an existing Chrome via CDP.

    Never close the operator's attached browser or existing tabs. A browser
    launched by this pilot is closed on exit, even on postal errors.
    """
    if cdp_url:
        browser = playwright.chromium.connect_over_cdp(cdp_url)
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
                "Abre YZA en ese Chrome, selecciona CP 11590 y deja "
                "la pestaña abierta antes del piloto."
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



def verify_operator_ready_category(page, category_url: str, trace: dict | None = None) -> str:
    """Continue from an operator-prepared YZA tab, without editing the CP.

    The visible applied 11590 must be proved BEFORE navigating to the
    category, and verified again AFTER navigation. Existing popup inputs,
    cookies and an operator assertion alone are never sufficient proof.
    """
    if trace is None:
        trace = {}
    trace["mode"] = "operator_prepared_chrome"
    trace["stage"] = "inspect_open_yza_tab"
    if urlparse(page.url).netloc.lower() not in {HOST, "yza.mx"}:
        trace["stage"] = "wrong_browser_tab"
        raise YzaContextError(
            "El Chrome conectado no tiene una pestaña activa de YZA."
        )

    trace["stage"] = "verify_operator_postal_before_navigation"
    initial = context_evidence(page)
    trace["postal_visible_before_navigation"] = bool(initial)
    if not initial:
        trace["stage"] = "operator_postal_not_verified"
        raise YzaContextError(
            "El CP 11590 no aparece aplicado en la cabecera de YZA. "
            "Selecciona 11590 en el popup, pulsa ACEPTAR, confirma la "
            "ubicación en la página y vuelve a ejecutar el piloto. "
            "El scraper no modificará el CP de tu Chrome."
        )

    source = urlparse(page.url)
    target = urlparse(category_url)
    if target.scheme != "https" or target.netloc != HOST:
        raise YzaContextError("URL de categoría YZA fuera del dominio permitido.")
    current_path = source.path.rstrip("/")
    target_path = target.path.rstrip("/")
    if current_path != target_path or source.query != target.query:
        trace["stage"] = "navigate_to_category_with_existing_location"
        page.goto(category_url, wait_until="domcontentloaded", timeout=75000)
        page.wait_for_timeout(900)

    trace["stage"] = "verify_operator_postal_after_navigation"
    after = context_evidence(page)
    trace["postal_visible_after_navigation"] = bool(after)
    if not after:
        trace["stage"] = "operator_postal_lost_after_navigation"
        raise YzaContextError(
            "YZA no conserva el CP 11590 al navegar a esta categoría. "
            "No se certifican los precios; revisa la ubicación en Chrome."
        )
    trace["stage"] = "operator_postal_verified"
    return after


class YzaScraper:
    def __init__(self, cdp_url: str = "", max_pages: int = 100):
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
            with yza_browser_page(playwright, self.cdp_url) as page:
                if self.cdp_url:
                    # San Pablo-style continuation: operator has already
                    # opened YZA and applied 11590 in the same Chrome tab.
                    evidence = verify_operator_ready_category(page, category.url)
                else:
                    page.goto(category.url, wait_until="domcontentloaded", timeout=75000)
                    page.wait_for_timeout(800)
                    evidence = apply_postal_code(page)
                    if not evidence:
                        raise YzaContextError(
                            "CP 11590 NO confirmado en YZA. "
                            "No se atribuyen precios a una ubicación no verificada."
                        )
                self.run_meta["context_verified"] = True
                self.run_meta["context_evidence"] = evidence
                expected_last = None

                for number in range(1, self.max_pages + 1):
                    page.goto(page_url(category.url, number),
                              wait_until="domcontentloaded", timeout=75000)
                    page.wait_for_timeout(700)
                    if urlparse(page.url).netloc.lower() not in {HOST, "yza.mx"}:
                        raise YzaScrapeError("Redirección fuera de YZA")
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
                    if number > 1 and fresh == 0:
                        raise YzaScrapeError(
                            f"La página {number} repite todos los productos "
                            "o no tiene precios/SKU válidos. Paginación no verificada."
                        )
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
        return rows
