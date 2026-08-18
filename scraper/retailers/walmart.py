from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from playwright.sync_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from ..config import Category, Location
from ..parsers import absolute_url, clean_text, extract_sku, parse_money

BASE_URL = "https://www.walmart.com.mx/"
BLOCK_MARKERS = (
    "access denied",
    "robot or human",
    "captcha",
    "reference #18",
    "forbidden",
    "verifica tu identidad",
    "mantén presionado",
    "manten presionado",
)


class WalmartBlocked(RuntimeError):
    pass


class WalmartStoreContextError(RuntimeError):
    pass


class WalmartScraper:
    """Playwright scraper for public Walmart Mexico category pages.

    Store-specific rows are emitted only after the current browser session has
    produced explicit evidence for the configured store. Evidence can come from
    the storefront text, browser state, or a product detail page that explicitly
    identifies the pickup store. The scraper never solves identity challenges or
    bypasses access controls.
    """

    def __init__(
        self,
        headless: bool = True,
        diagnostics_dir: str | Path = "diagnostics",
        max_pages: int = 100,
        wait_ms: int = 1200,
        require_store_context: bool = True,
        store_only: bool = True,
    ) -> None:
        self.headless = headless
        self.diagnostics_dir = Path(diagnostics_dir)
        self.diagnostics_dir.mkdir(parents=True, exist_ok=True)
        self.max_pages = max_pages
        self.wait_ms = wait_ms
        self.require_store_context = require_store_context
        self.store_only = store_only
        self.run_meta: dict[str, Any] = {}
        self._active_store_context_method: str | None = None

    @staticmethod
    def _paged_url(url: str, page_number: int) -> str:
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        if page_number <= 1:
            query.pop("page", None)
        else:
            query["page"] = str(page_number)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    @staticmethod
    def _normalize(value: str | None) -> str:
        if not value:
            return ""
        value = unquote(value).lower()
        value = value.replace("á", "a").replace("é", "e").replace("í", "i")
        value = value.replace("ó", "o").replace("ú", "u").replace("ü", "u")
        return re.sub(r"\s+", " ", value).strip()

    def _save_diagnostics(self, page: Page, prefix: str) -> None:
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", prefix)
        try:
            page.screenshot(path=str(self.diagnostics_dir / f"walmart_{safe}.png"), full_page=True)
        except Exception:
            pass
        try:
            (self.diagnostics_dir / f"walmart_{safe}.html").write_text(page.content(), encoding="utf-8")
        except Exception:
            pass
        try:
            (self.diagnostics_dir / "walmart_run_meta.json").write_text(
                json.dumps(self.run_meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    def _body_text(self, page: Page) -> str:
        try:
            return page.locator("body").inner_text(timeout=8_000)
        except Exception:
            return ""

    def _assert_not_blocked(self, page: Page, status: int | None = None) -> None:
        body = self._normalize(self._body_text(page))
        blocked_url = "/blocked" in (page.url or "").lower()
        if status in (401, 403, 429) or blocked_url or any(self._normalize(marker) in body for marker in BLOCK_MARKERS):
            self._save_diagnostics(page, "blocked")
            raise WalmartBlocked(
                "Walmart bloqueó o desafió la sesión automatizada. Se guardaron diagnósticos; "
                "el scraper no intenta evadir la protección del sitio."
            )

    @classmethod
    def _store_context_in_text(cls, text: str, location: Location) -> bool:
        normalized = cls._normalize(text)
        store = cls._normalize(location.store)
        postal = cls._normalize(location.postal_code)
        store_id = cls._normalize(location.store_id)
        store_ok = bool(store and store in normalized)
        postal_ok = bool(postal and postal in normalized)
        store_id_ok = bool(store_id and (f"#{store_id}" in normalized or store_id in normalized))

        pickup_store_ok = False
        if store_ok:
            pickup_store_ok = bool(
                re.search(rf"pickup.{{0,100}}{re.escape(store)}", normalized)
                or re.search(rf"{re.escape(store)}.{{0,100}}pickup", normalized)
                or re.search(rf"recog.{{0,100}}{re.escape(store)}", normalized)
                or re.search(rf"{re.escape(store)}.{{0,100}}recog", normalized)
            )

        return store_ok and (postal_ok or store_id_ok or pickup_store_ok)

    @classmethod
    def _store_context_in_state_blob(cls, blob: str, location: Location) -> bool:
        normalized = cls._normalize(blob)
        store = cls._normalize(location.store)
        postal = cls._normalize(location.postal_code)
        store_id = cls._normalize(location.store_id)
        hits = 0
        if store and store in normalized:
            hits += 1
        if postal and postal in normalized:
            hits += 1
        if store_id and store_id in normalized:
            hits += 1
        return hits >= 2

    def _browser_state_blob(self, page: Page) -> str:
        payload: dict[str, Any] = {"cookies": [], "localStorage": {}, "sessionStorage": {}}
        try:
            payload["cookies"] = page.context.cookies()
        except Exception:
            pass
        try:
            payload.update(
                page.evaluate(
                    """
                    () => ({
                      localStorage: Object.fromEntries(Object.entries(localStorage)),
                      sessionStorage: Object.fromEntries(Object.entries(sessionStorage)),
                    })
                    """
                )
            )
        except Exception:
            pass
        return json.dumps(payload, ensure_ascii=False)

    def _store_context_in_browser_state(self, page: Page, location: Location) -> bool:
        return self._store_context_in_state_blob(self._browser_state_blob(page), location)

    def _try_select_store_ui(self, page: Page, location: Location) -> tuple[bool, str | None]:
        """Best-effort use of Walmart's normal fulfillment/store UI."""
        triggers = (
            "¿Cómo quieres tus artículos?",
            "Como quieres tus articulos",
            "Agregar dirección",
            "Agregar direccion",
        )
        for label in triggers:
            try:
                candidate = page.get_by_text(label, exact=False).first
                if candidate.count() and candidate.is_visible():
                    candidate.click(timeout=4_000)
                    page.wait_for_timeout(700)
                    break
            except Exception:
                continue

        for label in ("Pickup", "Recoger", "Recoge", "Recogida"):
            try:
                candidate = page.get_by_text(label, exact=False).first
                if candidate.count() and candidate.is_visible():
                    candidate.click(timeout=3_000)
                    page.wait_for_timeout(500)
                    break
            except Exception:
                continue

        inputs = page.locator("input")
        try:
            count = min(inputs.count(), 40)
        except Exception:
            count = 0
        for i in range(count):
            inp = inputs.nth(i)
            try:
                if not inp.is_visible():
                    continue
                hint = " ".join(
                    filter(
                        None,
                        [
                            inp.get_attribute("placeholder"),
                            inp.get_attribute("aria-label"),
                            inp.get_attribute("name"),
                        ],
                    )
                ).lower()
                if any(word in hint for word in ("código", "codigo", "postal", "ubic", "tienda", "store")):
                    inp.fill(location.postal_code or location.store or "")
                    inp.press("Enter")
                    page.wait_for_timeout(1_500)
                    break
            except Exception:
                continue

        if location.store:
            try:
                store_choice = page.get_by_text(location.store, exact=False).first
                if store_choice.count() and store_choice.is_visible():
                    store_choice.click(timeout=4_000)
                    page.wait_for_timeout(1_000)
            except Exception:
                pass

        for label in ("Usar esta tienda", "Elegir esta tienda", "Seleccionar", "Guardar", "Confirmar"):
            try:
                button = page.get_by_text(label, exact=False).first
                if button.count() and button.is_visible():
                    button.click(timeout=3_000)
                    page.wait_for_timeout(900)
                    break
            except Exception:
                continue

        body = self._body_text(page)
        if self._store_context_in_text(body, location):
            return True, "category_text_after_ui"
        if self._store_context_in_browser_state(page, location):
            return True, "browser_state_after_ui"
        return False, None

    def _establish_store_reference(self, page: Page, location: Location) -> None:
        """Open Walmart's store page as a reference; this alone is not proof."""
        if not location.store_id or not location.store:
            return

        store_url = f"https://www.walmart.com.mx/tienda/{location.store_id}"
        response = page.goto(store_url, wait_until="domcontentloaded", timeout=120_000)
        self._assert_not_blocked(page, response.status if response else None)
        page.wait_for_timeout(1_000)

        text = self._body_text(page)
        self.run_meta["store_reference_url"] = store_url
        self.run_meta["store_reference_page_matches"] = self._store_context_in_text(text, location)

        try:
            favorite = page.get_by_text("Guardar como mi tienda favorita", exact=False).first
            if favorite.count() and favorite.is_visible():
                favorite.click(timeout=4_000)
                page.wait_for_timeout(800)
                self.run_meta["favorite_store_action_attempted"] = True
        except Exception:
            self.run_meta["favorite_store_action_attempted"] = False

    def _product_candidates(self, page: Page, limit: int = 5) -> list[str]:
        try:
            raw = page.locator('a[href*="/ip/"]').evaluate_all(
                """
                (anchors, limit) => {
                  const out = [];
                  const seen = new Set();
                  for (const a of anchors) {
                    const href = a.getAttribute('href');
                    if (!href || seen.has(href)) continue;
                    let node = a;
                    let text = '';
                    for (let i = 0; i < 8 && node; i++, node = node.parentElement) {
                      text = (node.textContent || '').replace(/\s+/g, ' ').trim();
                      if (/pickup|recoge|recoger|recogida/i.test(text)) break;
                    }
                    if (/pickup|recoge|recoger|recogida/i.test(text)) {
                      seen.add(href);
                      out.push(href);
                    }
                    if (out.length >= limit) break;
                  }
                  return out;
                }
                """,
                limit,
            )
            return [absolute_url(x, BASE_URL) for x in raw if x]
        except Exception:
            return []

    def _verify_store_via_product_detail(self, page: Page, location: Location) -> tuple[bool, str | None]:
        candidates = self._product_candidates(page)
        self.run_meta["store_verification_product_candidates"] = candidates
        for url in candidates:
            detail = page.context.new_page()
            try:
                response = detail.goto(url, wait_until="domcontentloaded", timeout=120_000)
                self._assert_not_blocked(detail, response.status if response else None)
                detail.wait_for_timeout(self.wait_ms)
                text = self._body_text(detail)
                if self._store_context_in_text(text, location):
                    self.run_meta["store_verification_product_url"] = detail.url
                    return True, "product_detail_pickup_store"
                if self._store_context_in_browser_state(detail, location):
                    self.run_meta["store_verification_product_url"] = detail.url
                    return True, "product_detail_browser_state"
            finally:
                detail.close()
        return False, None

    def _verify_store_context(
        self,
        page: Page,
        location: Location,
        *,
        allow_ui: bool = True,
        allow_product_detail: bool = True,
    ) -> tuple[bool, str | None]:
        body = self._body_text(page)
        if self._store_context_in_text(body, location):
            return True, "category_text"
        if self._store_context_in_browser_state(page, location):
            return True, "browser_state"

        if allow_ui:
            verified, method = self._try_select_store_ui(page, location)
            if verified:
                return verified, method

        if allow_product_detail:
            verified, method = self._verify_store_via_product_detail(page, location)
            if verified:
                return verified, method

        return False, None

    @staticmethod
    def _infer_brand(product: str | None) -> str | None:
        if not product:
            return None
        known = [
            "Colgate", "Oral-B", "Sensodyne", "Listerine", "Crest", "Equate",
            "Corega", "Aquafresh", "Gum", "Curaprox", "Philips", "Marvis",
            "Ariel", "Persil", "Roma", "Suavitel", "Vanish", "Cloralex", "Clorox",
            "Zote", "Ace", "Bold", "Blanca Nieves", "MAS", "Lysol", "Oxiclean",
            "Dr. Beckmann", "Ensueño", "Great Value", "Arm & Hammer", "Tide", "Downy",
            "Carisma",
        ]
        lower = product.lower()
        for brand in known:
            if brand.lower() in lower:
                return brand
        return None

    def _extract_cards(self, page: Page, category: Category, location: Location) -> list[dict[str, Any]]:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        js = r"""
        anchors => {
          const money = /\$\s*[\d,.]+/;
          const seen = new Set();
          const out = [];
          for (const a of anchors) {
            const href = a.getAttribute('href');
            if (!href || seen.has(href)) continue;
            seen.add(href);

            let card = a;
            for (let i = 0; i < 10 && card && card.parentElement; i++) {
              const txt = (card.textContent || '').trim();
              if (money.test(txt) && txt.length > 20) break;
              card = card.parentElement;
            }
            if (!card) continue;
            const text = (card.textContent || '').replace(/\s+/g, ' ').trim();
            if (!money.test(text)) continue;

            const img = card.querySelector('img[alt]');
            const anchorText = (a.textContent || '').replace(/\s+/g, ' ').trim();
            const aria = a.getAttribute('aria-label');
            const product = (aria && aria.length > 4 ? aria : null) ||
                            (anchorText && anchorText.length > 4 ? anchorText : null) ||
                            (img ? img.getAttribute('alt') : null);
            out.push({href, product, text});
          }
          return out;
        }
        """
        anchors = page.locator('a[href*="/ip/"]')
        if not anchors.count():
            return []
        raw = anchors.evaluate_all(js)
        rows: list[dict[str, Any]] = []

        for item in raw:
            url = absolute_url(item.get("href"), BASE_URL)
            sku = extract_sku(url)
            product = clean_text(item.get("product"))
            text = clean_text(item.get("text")) or ""
            if not sku or not product:
                continue

            current_match = re.search(r"precio\s+actual\s*(?:MXN)?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", text, re.I)
            before_match = re.search(r"(?:Antes|costaba)\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", text, re.I)
            current = parse_money(current_match.group(1)) if current_match else None
            regular = parse_money(before_match.group(1)) if before_match else None

            if current is None:
                tokens = re.findall(r"\$\s*[\d,]+(?:\.\d{1,2})?", text)
                values = [parse_money(x) for x in tokens]
                values = [x for x in values if x is not None]
                current = values[0] if values else None
                regular = regular or (values[1] if len(values) > 1 else current)
            if regular is None:
                regular = current

            pickup = bool(re.search(r"\b(pickup|recoge|recoger|recogida)\b", text, re.I))
            if self.store_only and not pickup:
                continue

            promos = []
            for pattern in (
                r"Rebaja",
                r"Precio en línea",
                r"Más vendido",
                r"Combina\s+\d+\s*x\s*\$[\d,.]+",
                r"Ahorra\s*\$[\d,.]+",
            ):
                m = re.search(pattern, text, re.I)
                if m:
                    promos.append(m.group(0))

            rows.append(
                {
                    "scrape_timestamp": now,
                    "retailer": "Walmart",
                    "city": location.city,
                    "state": location.state,
                    "postal_code": location.postal_code,
                    "store": location.store,
                    "store_id": location.store_id,
                    "department": category.department,
                    "category": category.name,
                    "subcategory": category.subcategory,
                    "sub_subcategory": category.sub_subcategory,
                    "category_id": category.id,
                    "sku": sku,
                    "brand": self._infer_brand(product),
                    "product": product,
                    "price_current": current,
                    "price_regular": regular,
                    "promotion": clean_text(" | ".join(dict.fromkeys(promos))),
                    "pickup_available": pickup,
                    "store_context_verified": bool(self._active_store_context_method),
                    "store_context_method": self._active_store_context_method,
                    "url": url,
                    "price_raw": text,
                }
            )
        return rows

    def _scrape_with_context(
        self,
        context: BrowserContext,
        category: Category,
        location: Location,
        *,
        establish_reference: bool,
    ) -> list[dict[str, Any]]:
        page = context.pages[0] if context.pages else context.new_page()
        rows: list[dict[str, Any]] = []
        seen_skus: set[str] = set()
        no_new_pages = 0
        session_verified = not self.require_store_context
        verification_method: str | None = None

        if establish_reference:
            self._establish_store_reference(page, location)

        for page_number in range(1, self.max_pages + 1):
            url = self._paged_url(category.url, page_number)
            response = page.goto(url, wait_until="domcontentloaded", timeout=120_000)
            self._assert_not_blocked(page, response.status if response else None)
            page.wait_for_timeout(self.wait_ms)

            try:
                page.wait_for_selector('a[href*="/ip/"]', timeout=20_000)
            except PlaywrightTimeoutError:
                self._save_diagnostics(page, f"no_products_page_{page_number}")
                break

            if not session_verified:
                session_verified, verification_method = self._verify_store_context(
                    page,
                    location,
                    allow_ui=True,
                    allow_product_detail=True,
                )
                if not session_verified:
                    self._save_diagnostics(page, f"store_context_missing_page_{page_number}")
                    raise WalmartStoreContextError(
                        f"No se pudo probar el contexto de {location.store} / {location.postal_code}. "
                        "Se guardaron diagnósticos; no se etiquetaron precios como datos de tienda."
                    )
                self._active_store_context_method = verification_method
                self.run_meta["store_context_verified"] = True
                self.run_meta["store_context_method"] = verification_method
                self.run_meta["store_context_verified_on_page"] = page_number
            else:
                direct_verified, direct_method = self._verify_store_context(
                    page,
                    location,
                    allow_ui=False,
                    allow_product_detail=False,
                )
                page_method = direct_method or f"session:{verification_method or 'preverified'}"
                self._active_store_context_method = verification_method or direct_method or "preverified"
                self.run_meta.setdefault("pages", []).append(
                    {
                        "page": page_number,
                        "url": page.url,
                        "store_context_verified": True,
                        "store_context_method": page_method,
                        "direct_store_evidence": direct_verified,
                    }
                )

            if not self.run_meta.get("pages") or self.run_meta["pages"][-1].get("page") != page_number:
                self.run_meta.setdefault("pages", []).append(
                    {
                        "page": page_number,
                        "url": page.url,
                        "store_context_verified": session_verified,
                        "store_context_method": verification_method,
                        "direct_store_evidence": True,
                    }
                )

            page_rows = self._extract_cards(page, category, location)
            new_rows = [r for r in page_rows if r["sku"] not in seen_skus]
            for row in new_rows:
                seen_skus.add(row["sku"])
            rows.extend(new_rows)

            self.run_meta["pages"][-1]["rows"] = len(page_rows)
            self.run_meta["pages"][-1]["new_rows"] = len(new_rows)

            no_new_pages = no_new_pages + 1 if not new_rows else 0
            if no_new_pages >= 2:
                break

        self.run_meta["store_context_verified"] = session_verified
        self.run_meta["unique_products"] = len(seen_skus)
        self._save_diagnostics(page, f"success_{category.id}_{location.id}")
        return rows

    def scrape_category(self, category: Category, location: Location) -> list[dict[str, Any]]:
        if self.require_store_context and (not location.store_id or not location.store):
            raise WalmartStoreContextError("Walmart requiere una tienda configurada para esta corrida.")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context: BrowserContext = browser.new_context(
                locale="es-MX",
                viewport={"width": 1440, "height": 1000},
            )
            try:
                return self._scrape_with_context(
                    context,
                    category,
                    location,
                    establish_reference=True,
                )
            finally:
                context.close()
                browser.close()
