from __future__ import annotations

import json
import re
import time
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
        manual_verification_timeout_ms: int = 180_000,
    ) -> None:
        self.headless = headless
        self.diagnostics_dir = Path(diagnostics_dir)
        self.diagnostics_dir.mkdir(parents=True, exist_ok=True)
        self.max_pages = max_pages
        self.wait_ms = wait_ms
        self.require_store_context = require_store_context
        self.store_only = store_only
        self.manual_verification_timeout_ms = manual_verification_timeout_ms
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

    def _is_blocked(self, page: Page, status: int | None = None) -> bool:
        body = self._normalize(self._body_text(page))
        blocked_url = "/blocked" in (page.url or "").lower()
        return bool(
            status in (401, 403, 429)
            or blocked_url
            or any(self._normalize(marker) in body for marker in BLOCK_MARKERS)
        )

    def _assert_not_blocked(self, page: Page, status: int | None = None) -> None:
        if not self._is_blocked(page, status):
            return

        self._save_diagnostics(page, "blocked")

        # In a visible browser, give the human operator time to complete any
        # identity challenge manually. The scraper does not interact with or
        # solve the challenge; it only waits for the storefront to become
        # available again.
        if not self.headless:
            self.run_meta["manual_verification_required"] = True
            self.run_meta["manual_verification_resolved"] = False
            print(
                "MANUAL_VERIFICATION_REQUIRED: completa la verificación de Walmart "
                "en Chromium. El scraper esperará hasta 3 minutos.",
                flush=True,
            )
            deadline = time.monotonic() + (self.manual_verification_timeout_ms / 1000)
            while time.monotonic() < deadline:
                page.wait_for_timeout(1_000)
                if not self._is_blocked(page):
                    self.run_meta["manual_verification_resolved"] = True
                    self._save_diagnostics(page, "manual_verification_resolved")
                    print("MANUAL_VERIFICATION_RESOLVED: continuando.", flush=True)
                    return

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

    @classmethod
    def _is_store_location_input_metadata(
        cls,
        *,
        input_type: str | None,
        role: str | None,
        placeholder: str | None,
        aria_label: str | None,
        name: str | None,
        element_id: str | None,
    ) -> bool:
        metadata = " ".join(
            x for x in (placeholder, aria_label, name, element_id) if x
        )
        normalized = cls._normalize(metadata)
        input_type_normalized = cls._normalize(input_type)
        role_normalized = cls._normalize(role)

        # Never treat the global product search box as a postal/store field.
        if input_type_normalized == "search" or role_normalized == "searchbox":
            return False
        if any(marker in normalized for marker in ("buscar", "search", "busqueda", "query")):
            return False
        if cls._normalize(name) in {"q", "query", "search"}:
            return False

        return any(
            marker in normalized
            for marker in (
                "codigo postal",
                "postal",
                "zip code",
                "zipcode",
                "ubicacion",
                "direccion",
                "domicilio",
            )
        )

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

        postal_filled = False
        postal_value = location.postal_code or ""
        starting_url = page.url
        for i in range(count):
            inp = inputs.nth(i)
            try:
                if not inp.is_visible():
                    continue

                metadata = {
                    "input_type": inp.get_attribute("type"),
                    "role": inp.get_attribute("role"),
                    "placeholder": inp.get_attribute("placeholder"),
                    "aria_label": inp.get_attribute("aria-label"),
                    "name": inp.get_attribute("name"),
                    "element_id": inp.get_attribute("id"),
                }
                if not self._is_store_location_input_metadata(**metadata):
                    continue

                inp.fill(postal_value)
                postal_filled = True
                self.run_meta["postal_input_metadata"] = metadata

                submitted = False
                for label in (
                    "Continuar",
                    "Aplicar",
                    "Buscar tiendas",
                    "Ver tiendas",
                    "Guardar ubicación",
                    "Guardar ubicacion",
                ):
                    try:
                        button = page.get_by_text(label, exact=False).first
                        if button.count() and button.is_visible():
                            button.click(timeout=3_000)
                            submitted = True
                            break
                    except Exception:
                        continue

                if not submitted:
                    inp.press("Enter")

                page.wait_for_timeout(1_500)
                break
            except Exception:
                continue

        self.run_meta["postal_filled"] = postal_filled

        # Defensive recovery: an earlier Walmart selector could accidentally
        # submit the postal code through the global search box. Never scrape a
        # /search?q=<postal> page as if it were the configured category.
        current = urlsplit(page.url)
        current_query = dict(parse_qsl(current.query, keep_blank_values=True))
        if (
            postal_value
            and current.path.rstrip("/").endswith("/search")
            and current_query.get("q") == postal_value
        ):
            self.run_meta["accidental_postal_search_redirect"] = page.url
            page.goto(starting_url, wait_until="domcontentloaded", timeout=120_000)
            page.wait_for_timeout(1_000)
            postal_filled = False
            self.run_meta["postal_filled"] = False

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
          const currentPrice = /precio\s+actual/i;
          const seen = new Set();
          const out = [];

          for (const a of anchors) {
            const rawHref = a.getAttribute('href');
            if (!rawHref) continue;

            let href = null;
            try {
              const parsed = new URL(rawHref, window.location.origin);
              if (
                parsed.hostname === window.location.hostname &&
                /^\/ip\//i.test(parsed.pathname)
              ) {
                href = parsed.pathname + parsed.search;
              }
            } catch (e) {}

            // Ignore sponsored tracking URLs such as /wapcrs/track?...rd=.../ip/...
            // The canonical /ip/ anchor for the same product is present separately.
            if (!href || seen.has(href)) continue;

            // Walmart and Bodega render sponsored brand banners inside the
            // category stream. These banners can contain canonical /ip/ links
            // and real prices, but they are not category product cards.
            let sponsored = false;
            let sponsorProbe = a;
            for (
              let depth = 0;
              depth < 10 && sponsorProbe;
              depth++, sponsorProbe = sponsorProbe.parentElement
            ) {
              const probeText = (
                sponsorProbe.innerText ||
                sponsorProbe.textContent ||
                ''
              ).replace(/\s+/g, ' ').trim().toLowerCase();
              const probeClass = (
                sponsorProbe.getAttribute('class') || ''
              ).toLowerCase();
              const probeTestId = (
                sponsorProbe.getAttribute('data-testid') ||
                sponsorProbe.getAttribute('data-automation-id') ||
                ''
              ).toLowerCase();

              if (
                probeText.includes('patrocinado por') ||
                probeText.includes('sponsored by') ||
                probeClass.includes('sponsor') ||
                probeTestId.includes('sponsor')
              ) {
                sponsored = true;
                break;
              }
            }
            if (sponsored) continue;

            seen.add(href);

            let card = a;
            for (let i = 0; i < 12 && card && card.parentElement; i++) {
              const txt = (card.textContent || '').replace(/\s+/g, ' ').trim();
              if ((currentPrice.test(txt) || money.test(txt)) && txt.length > 20) break;
              card = card.parentElement;
            }
            if (!card) continue;

            const text = (card.textContent || '').replace(/\s+/g, ' ').trim();
            if (!currentPrice.test(text) && !money.test(text)) continue;

            const titleNode = card.querySelector(
              '[data-automation-id="product-title"], [data-testid="product-title"]'
            );
            const img = card.querySelector('img[alt]');
            const anchorText = (a.textContent || '').replace(/\s+/g, ' ').trim();
            const aria = a.getAttribute('aria-label');
            const title = titleNode ? (titleNode.textContent || '').trim() : null;

            const product =
              (title && title.length > 4 ? title : null) ||
              (aria && aria.length > 4 && !/^destacado$/i.test(aria) ? aria : null) ||
              (img && img.getAttribute('alt') ? img.getAttribute('alt') : null) ||
              (anchorText && anchorText.length > 4 && !/^destacado$/i.test(anchorText) ? anchorText : null);

            const pickup = /\b(pickup|recoge|recoger|recogida)\b/i.test(text);
            out.push({href, product, text, pickup});
          }
          return out;
        }
        """

        anchors = page.locator('a[href*="/ip/"]')
        anchor_count = anchors.count()
        if not anchor_count:
            self.run_meta.setdefault("card_extraction", []).append(
                {"anchors": 0, "candidates": 0, "pickup_signals": 0, "rows": 0}
            )
            return []

        raw = anchors.evaluate_all(js)
        candidates: list[dict[str, Any]] = []
        rejected_missing_identity = 0
        rejected_missing_price = 0

        for item in raw:
            url = absolute_url(item.get("href"), BASE_URL)
            sku = extract_sku(url)
            product = clean_text(item.get("product"))
            text = clean_text(item.get("text")) or ""
            if not sku or not product:
                rejected_missing_identity += 1
                continue

            current_match = re.search(
                r"precio\s+actual\s*(?:MXN)?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)",
                text,
                re.I,
            )
            before_match = re.search(
                r"(?:Antes|costaba)\s*\$?\s*([\d,]+(?:\.\d{1,2})?)",
                text,
                re.I,
            )
            current = parse_money(current_match.group(1)) if current_match else None
            regular = parse_money(before_match.group(1)) if before_match else None

            if current is None:
                tokens = re.findall(r"\$\s*[\d,]+(?:\.\d{1,2})?", text)
                values = [parse_money(x) for x in tokens]
                values = [x for x in values if x is not None]
                current = values[0] if values else None
                regular = regular or (values[1] if len(values) > 1 else current)

            if current is None:
                rejected_missing_price += 1
                continue
            if regular is None:
                regular = current

            candidates.append(
                {
                    "url": url,
                    "sku": sku,
                    "product": product,
                    "text": text,
                    "price_current": current,
                    "price_regular": regular,
                    "pickup": bool(item.get("pickup")),
                }
            )

        pickup_signal_count = sum(1 for item in candidates if item["pickup"])
        enforce_card_pickup = self.store_only and pickup_signal_count > 0

        # Walmart currently scopes category prices to the verified store but may
        # omit pickup text from every product tile. In that case, keep the rows
        # while leaving pickup_available unknown instead of inventing pickup
        # availability or discarding the entire verified category.
        if self.store_only and pickup_signal_count == 0:
            self.run_meta["pickup_filter_mode"] = "verified_store_context_no_card_signal"
        elif self.store_only:
            self.run_meta["pickup_filter_mode"] = "card_pickup_signal"
        else:
            self.run_meta["pickup_filter_mode"] = "disabled"

        rows: list[dict[str, Any]] = []
        rejected_no_pickup = 0
        for item in candidates:
            if enforce_card_pickup and not item["pickup"]:
                rejected_no_pickup += 1
                continue

            promos = []
            for pattern in (
                r"Rebaja",
                r"Precio en línea",
                r"Más vendido",
                r"Combina\s+\d+\s*x\s*\$[\d,.]+",
                r"Ahorra\s*\$[\d,.]+",
            ):
                match = re.search(pattern, item["text"], re.I)
                if match:
                    promos.append(match.group(0))

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
                    "sku": item["sku"],
                    "brand": self._infer_brand(item["product"]),
                    "product": item["product"],
                    "price_current": item["price_current"],
                    "price_regular": item["price_regular"],
                    "promotion": clean_text(" | ".join(dict.fromkeys(promos))),
                    "pickup_available": (
                        item["pickup"] if pickup_signal_count > 0 else None
                    ),
                    "store_context_verified": bool(self._active_store_context_method),
                    "store_context_method": self._active_store_context_method,
                    "url": item["url"],
                    "price_raw": item["text"],
                }
            )

        self.run_meta.setdefault("card_extraction", []).append(
            {
                "anchors": anchor_count,
                "canonical_candidates": len(raw),
                "candidates_with_identity_and_price": len(candidates),
                "pickup_signals": pickup_signal_count,
                "rejected_missing_identity": rejected_missing_identity,
                "rejected_missing_price": rejected_missing_price,
                "rejected_no_pickup": rejected_no_pickup,
                "rows": len(rows),
            }
        )
        return rows

    @staticmethod
    def _target_count(page: Page) -> int | None:
        try:
            text = page.locator("body").inner_text(timeout=5_000)
        except Exception:
            return None

        values: list[int] = []
        patterns = (
            r"\b([0-9][0-9,.]{0,8})\s+productos?\b",
            r"\b([0-9][0-9,.]{0,8})\s+resultados?\b",
            r"\bde\s+([0-9][0-9,.]{0,8})\s+productos?\b",
        )
        for pattern in patterns:
            for raw in re.findall(pattern, text, flags=re.IGNORECASE):
                try:
                    values.append(
                        int(raw.replace(",", "").replace(".", ""))
                    )
                except ValueError:
                    continue
        return max(values) if values else None

    @staticmethod
    def _pagination_snapshot(page) -> dict:
        """Find the visible numeric paginator shown below the product grid."""
        try:
            result = page.locator("body").evaluate(
                r"""
                () => {
                  const normalize = value =>
                    String(value || '').replace(/\s+/g, ' ').trim();

                  const visible = el => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return (
                      style.display !== 'none' &&
                      style.visibility !== 'hidden' &&
                      rect.width > 0 &&
                      rect.height > 0
                    );
                  };

                  const disabled = el =>
                    el.disabled === true ||
                    el.getAttribute('aria-disabled') === 'true';

                  const controlNumber = el => {
                    const text = normalize(el.innerText || el.textContent);
                    const aria = normalize(el.getAttribute('aria-label'));
                    const title = normalize(el.getAttribute('title'));

                    if (/^\d{1,4}$/.test(text)) {
                      return Number(text);
                    }

                    for (const value of [aria, title]) {
                      const match = value.match(
                        /(?:page|p[aá]gina)\s*(\d{1,4})/i
                      );
                      if (match) return Number(match[1]);
                    }
                    return null;
                  };

                  const candidateSet = new Set();
                  const selectors = [
                    '[aria-label*="pagin" i]',
                    '[aria-label*="page" i]',
                    '[data-testid*="pagin" i]',
                    '[data-automation-id*="pagin" i]',
                    '[class*="pagin" i]',
                    'nav',
                    '[role="navigation"]'
                  ];

                  for (const selector of selectors) {
                    for (const el of document.querySelectorAll(selector)) {
                      if (visible(el)) candidateSet.add(el);
                    }
                  }

                  // Fallback for pagers without semantic container metadata:
                  // walk up from visible numeric controls and score ancestors.
                  for (const control of document.querySelectorAll(
                    'button,a,[role="button"]'
                  )) {
                    if (!visible(control)) continue;
                    if (controlNumber(control) === null) continue;

                    let parent = control.parentElement;
                    for (let depth = 0; depth < 5 && parent; depth++) {
                      if (visible(parent)) candidateSet.add(parent);
                      parent = parent.parentElement;
                    }
                  }

                  let best = null;
                  for (const container of candidateSet) {
                    const controls = Array.from(
                      container.querySelectorAll(
                        'button,a,[role="button"]'
                      )
                    ).filter(visible);

                    const controlNumbers = Array.from(
                      new Set(
                        controls
                          .map(controlNumber)
                          .filter(value => Number.isFinite(value))
                      )
                    ).sort((a, b) => a - b);

                    if (controlNumbers.length < 2) continue;

                    const text = normalize(
                      container.innerText || container.textContent
                    );

                    // Bodega renders the final page after the ellipsis as
                    // plain text/span in some catalogue layouts, e.g.
                    // "1 2 3 … 23 >". Read those short pager-only numeric
                    // tokens as well; do not inspect large catalogue text.
                    const textNumbers = (
                      text.length <= 220 &&
                      (
                        text.includes('…') ||
                        text.includes('...') ||
                        /[>›»→]/.test(text)
                      )
                    )
                      ? Array.from(
                          new Set(
                            (text.match(/\b\d{1,4}\b/g) || [])
                              .map(value => Number(value))
                              .filter(value =>
                                Number.isFinite(value) &&
                                value >= 1 &&
                                value <= 9999
                              )
                          )
                        ).sort((a, b) => a - b)
                      : [];

                    const numbers = Array.from(
                      new Set([
                        ...controlNumbers,
                        ...textNumbers
                      ])
                    ).sort((a, b) => a - b);

                    const currentControl = controls.find(el =>
                      String(el.getAttribute('aria-current') || '')
                        .toLowerCase() === 'page'
                    );
                    const current = currentControl
                      ? controlNumber(currentControl)
                      : null;

                    const nextControls = controls.filter(el => {
                      const blob = normalize(
                        [
                          el.innerText,
                          el.textContent,
                          el.getAttribute('aria-label'),
                          el.getAttribute('title'),
                          el.getAttribute('data-testid'),
                          el.getAttribute('data-automation-id')
                        ].join(' ')
                      ).toLowerCase();
                      return (
                        /\b(next|siguiente|pr[oó]xima)\b/i.test(blob) ||
                        /^[>›»→]$/.test(normalize(
                          el.innerText || el.textContent
                        ))
                      );
                    });

                    const numericControls = controls.filter(
                      el => Number.isFinite(controlNumber(el))
                    );
                    const numericRightEdge = numericControls.length
                      ? Math.max(
                          ...numericControls.map(
                            el => el.getBoundingClientRect().right
                          )
                        )
                      : -Infinity;

                    const geometricNextControls = controls.filter(el => {
                      if (controlNumber(el) !== null) return false;
                      const label = normalize(
                        el.innerText || el.textContent
                      );
                      if (/^(\.\.\.|…)$/.test(label)) return false;
                      return (
                        el.getBoundingClientRect().left >=
                        numericRightEdge - 2
                      );
                    });

                    const allNextControls = Array.from(
                      new Set([
                        ...nextControls,
                        ...geometricNextControls
                      ])
                    );
                    const hasNext = allNextControls.length > 0;
                    const nextEnabled = allNextControls.some(
                      el => !disabled(el)
                    );

                    const score =
                      controlNumbers.length * 20 +
                      (text.includes('…') || text.includes('...') ? 25 : 0) +
                      (textNumbers.length > controlNumbers.length ? 20 : 0) +
                      (hasNext ? 10 : 0) +
                      (current !== null ? 5 : 0);

                    if (!best || score > best.score) {
                      best = {
                        score,
                        numbers,
                        control_numbers: controlNumbers,
                        text_numbers: textNumbers,
                        current,
                        max_page: Math.max(...numbers),
                        has_next: hasNext,
                        next_enabled: nextEnabled,
                        text: text.slice(0, 500)
                      };
                    }
                  }

                  return best || {
                    score: 0,
                    numbers: [],
                    current: null,
                    max_page: null,
                    has_next: false,
                    next_enabled: false,
                    text: ''
                  };
                }
                """
            )
        except Exception:
            return {
                "score": 0,
                "numbers": [],
                "current": None,
                "max_page": None,
                "has_next": False,
                "next_enabled": False,
                "text": "",
            }

        return result or {
            "score": 0,
            "numbers": [],
            "current": None,
            "max_page": None,
            "has_next": False,
            "next_enabled": False,
            "text": "",
        }

    def _scroll_to_pagination(
        self,
        page,
        *,
        max_scrolls: int = 60,
    ) -> dict:
        """Scroll gradually until the numeric pager becomes visible."""
        snapshot: dict = {}
        last_y = -1

        for round_number in range(1, max_scrolls + 1):
            snapshot = self._pagination_snapshot(page)
            if (
                snapshot.get("max_page")
                and len(snapshot.get("numbers") or []) >= 2
            ):
                snapshot["scroll_rounds"] = round_number - 1
                return snapshot

            try:
                position = page.evaluate(
                    r"""
                    () => {
                      const step = Math.max(
                        700,
                        Math.floor(window.innerHeight * 0.80)
                      );
                      window.scrollBy({top: step, behavior: 'auto'});
                      return {
                        y: Math.floor(window.scrollY),
                        bottom:
                          window.scrollY + window.innerHeight >=
                          document.documentElement.scrollHeight - 8
                      };
                    }
                    """
                )
            except Exception:
                position = {"y": last_y, "bottom": False}

            page.wait_for_timeout(
                min(max(int(self.wait_ms * 0.45), 800), 1_500)
            )

            current_y = int((position or {}).get("y") or 0)
            at_bottom = bool((position or {}).get("bottom"))
            if at_bottom and current_y == last_y:
                break
            last_y = current_y

        snapshot = self._pagination_snapshot(page)
        snapshot["scroll_rounds"] = max_scrolls
        return snapshot

    @staticmethod
    def _click_pagination_next(
        page,
        *,
        expected_page: int,
    ) -> dict:
        """Click the next numbered page (or the pager arrow) inside pager only."""
        try:
            result = page.locator("body").evaluate(
                r"""
                ({expectedPage}) => {
                  const normalize = value =>
                    String(value || '').replace(/\s+/g, ' ').trim();

                  const visible = el => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return (
                      style.display !== 'none' &&
                      style.visibility !== 'hidden' &&
                      rect.width > 0 &&
                      rect.height > 0
                    );
                  };

                  const disabled = el =>
                    el.disabled === true ||
                    el.getAttribute('aria-disabled') === 'true';

                  const controlNumber = el => {
                    const text = normalize(el.innerText || el.textContent);
                    const aria = normalize(el.getAttribute('aria-label'));
                    const title = normalize(el.getAttribute('title'));

                    if (/^\d{1,4}$/.test(text)) return Number(text);
                    for (const value of [aria, title]) {
                      const match = value.match(
                        /(?:page|p[aá]gina)\s*(\d{1,4})/i
                      );
                      if (match) return Number(match[1]);
                    }
                    return null;
                  };

                  const candidates = [];
                  for (const container of document.querySelectorAll(
                    '[aria-label*="pagin" i], [aria-label*="page" i], ' +
                    '[data-testid*="pagin" i], ' +
                    '[data-automation-id*="pagin" i], ' +
                    '[class*="pagin" i], nav, [role="navigation"]'
                  )) {
                    if (!visible(container)) continue;
                    const controls = Array.from(
                      container.querySelectorAll(
                        'button,a,[role="button"]'
                      )
                    ).filter(visible);
                    const nums = controls
                      .map(controlNumber)
                      .filter(value => Number.isFinite(value));
                    if (new Set(nums).size >= 2) {
                      candidates.push({container, controls, nums});
                    }
                  }

                  // Fallback from visible numeric controls.
                  if (!candidates.length) {
                    const numericControls = Array.from(
                      document.querySelectorAll(
                        'button,a,[role="button"]'
                      )
                    ).filter(el =>
                      visible(el) &&
                      Number.isFinite(controlNumber(el))
                    );
                    for (const control of numericControls) {
                      let parent = control.parentElement;
                      for (let depth = 0; depth < 5 && parent; depth++) {
                        const controls = Array.from(
                          parent.querySelectorAll(
                            'button,a,[role="button"]'
                          )
                        ).filter(visible);
                        const nums = controls
                          .map(controlNumber)
                          .filter(value => Number.isFinite(value));
                        if (new Set(nums).size >= 2) {
                          candidates.push({container: parent, controls, nums});
                          break;
                        }
                        parent = parent.parentElement;
                      }
                    }
                  }

                  if (!candidates.length) {
                    return {clicked: false, method: 'pager_not_found'};
                  }

                  candidates.sort(
                    (a, b) =>
                      new Set(b.nums).size - new Set(a.nums).size
                  );
                  const pager = candidates[0];

                  let target = pager.controls.find(
                    el =>
                      !disabled(el) &&
                      controlNumber(el) === expectedPage
                  );
                  let method = 'number';

                  if (!target) {
                    target = pager.controls.find(el => {
                      if (disabled(el)) return false;
                      const blob = normalize(
                        [
                          el.innerText,
                          el.textContent,
                          el.getAttribute('aria-label'),
                          el.getAttribute('title'),
                          el.getAttribute('data-testid'),
                          el.getAttribute('data-automation-id')
                        ].join(' ')
                      ).toLowerCase();
                      return (
                        /\b(next|siguiente|pr[oó]xima)\b/i.test(blob) ||
                        /^[>›»→]$/.test(
                          normalize(el.innerText || el.textContent)
                        )
                      );
                    });
                    method = 'next';
                  }

                  if (!target) {
                    const numericControls = pager.controls.filter(
                      el => Number.isFinite(controlNumber(el))
                    );
                    const rightEdge = numericControls.length
                      ? Math.max(
                          ...numericControls.map(
                            el => el.getBoundingClientRect().right
                          )
                        )
                      : -Infinity;

                    const rightSideControls = pager.controls
                      .filter(el => {
                        if (disabled(el)) return false;
                        if (controlNumber(el) !== null) return false;

                        const label = normalize(
                          el.innerText || el.textContent
                        );
                        if (/^(\.\.\.|…)$/.test(label)) return false;

                        const rect = el.getBoundingClientRect();
                        return rect.left >= rightEdge - 2;
                      })
                      .sort(
                        (a, b) =>
                          a.getBoundingClientRect().left -
                          b.getBoundingClientRect().left
                      );

                    target = rightSideControls[0] || null;
                    method = 'right_of_numeric_controls';
                  }

                  if (!target) {
                    return {clicked: false, method: 'next_not_found'};
                  }

                  target.scrollIntoView({
                    block: 'center',
                    inline: 'center',
                    behavior: 'auto'
                  });
                  target.click();

                  return {
                    clicked: true,
                    method,
                    expected_page: expectedPage,
                    label: normalize(
                      target.getAttribute('aria-label') ||
                      target.innerText ||
                      target.textContent ||
                      target.getAttribute('title')
                    )
                  };
                }
                """,
                {"expectedPage": expected_page},
            )
        except Exception as exc:
            return {
                "clicked": False,
                "method": "exception",
                "error": f"{type(exc).__name__}: {exc}",
            }

        return result or {
            "clicked": False,
            "method": "unknown",
        }

    def _collect_browse_source(
        self,
        page,
        source_url: str,
        category: Category,
        location: Location,
        seen_skus: set[str],
    ) -> tuple[list[dict], dict]:
        """Traverse the actual numeric UI pager one page at a time."""
        self._goto(page, source_url)

        try:
            page.wait_for_selector(
                'a[href*="/ip/"]',
                timeout=25_000,
            )
        except PlaywrightTimeoutError:
            self._save_diagnostics(
                page,
                "no_products_browse_"
                + str(len(self.run_meta.get("sources", [])) + 1),
            )
            return [], {
                "source_url": source_url,
                "actual_url": page.url,
                "rows": 0,
                "new_rows": 0,
                "target_products": self._target_count(page),
                "coverage": 0.0,
                "coverage_threshold": 0.90,
                "product_links": 0,
                "stabilized": False,
                "pagination_complete": False,
                "pages_visited": 0,
                "pagination_method": "ui_numeric",
                "no_product_links": True,
                "explicit_page_meta": [],
            }

        source_target = self._target_count(page)
        unique_source: dict[str, dict] = {}
        page_meta: list[dict] = []
        pagination_complete = False
        page_number = 1
        observed_last_page: int | None = None
        click_result: dict = {
            "clicked": True,
            "method": "initial_page",
        }

        while page_number <= max(1, self.max_pages):
            pager_snapshot = self._scroll_to_pagination(page)

            snapshot_last = pager_snapshot.get("max_page")
            try:
                snapshot_last = (
                    int(snapshot_last)
                    if snapshot_last not in (None, "")
                    else None
                )
            except (TypeError, ValueError):
                snapshot_last = None

            if snapshot_last is not None and snapshot_last >= 1:
                observed_last_page = max(
                    observed_last_page or 1,
                    snapshot_last,
                )

            rows = self._extract_rows(
                page,
                category,
                location,
            )

            unique_before = len(unique_source)
            for row in rows:
                sku = clean_text(str(row.get("sku") or ""))
                if sku:
                    unique_source[sku] = row
            new_on_page = len(unique_source) - unique_before

            pager_current = pager_snapshot.get("current")
            pager_proves_advance = bool(
                pager_current is not None
                and int(pager_current) == page_number
            )
            page_valid = bool(
                page_number == 1
                or new_on_page > 0
                or pager_proves_advance
            )
            if not page_valid:
                pagination_complete = False

            page_meta.append(
                {
                    "page_number": page_number,
                    "actual_url": page.url,
                    "rows": len(rows),
                    "new_unique_rows": new_on_page,
                    "unique_source": len(unique_source),
                    "product_links": self._product_link_count(page),
                    "pager_current": pager_current,
                    "pager_proves_advance": pager_proves_advance,
                    "pager_max_page": snapshot_last,
                    "observed_last_page": observed_last_page,
                    "pager_numbers": pager_snapshot.get("numbers"),
                    "pager_control_numbers": pager_snapshot.get(
                        "control_numbers"
                    ),
                    "pager_text_numbers": pager_snapshot.get(
                        "text_numbers"
                    ),
                    "pager_text": pager_snapshot.get("text"),
                    "pager_scroll_rounds": pager_snapshot.get(
                        "scroll_rounds"
                    ),
                    "has_next": bool(
                        pager_snapshot.get("has_next")
                    ),
                    "next_enabled": bool(
                        pager_snapshot.get("next_enabled")
                    ),
                    "click_result": click_result,
                    "stabilized": page_valid,
                }
            )

            page_display_total = (
                observed_last_page
                if observed_last_page is not None
                else "?"
            )
            print(
                "Bodega Aurrera UI page "
                f"{page_number}/{page_display_total}: "
                f"rows={len(rows)}, "
                f"new={new_on_page}, "
                f"unique_source={len(unique_source)}, "
                f"next_enabled={bool(pager_snapshot.get('next_enabled'))}",
                flush=True,
            )

            if not page_valid:
                break

            next_enabled = bool(
                pager_snapshot.get("next_enabled")
            )
            has_next = bool(
                pager_snapshot.get("has_next")
            )

            # The real stopping condition is dynamic: the current paginator
            # no longer exposes an enabled next control. The last-page number
            # is diagnostic only and may change between runs or even while a
            # catalogue is being served.
            if not next_enabled:
                if (
                    observed_last_page is None
                    or page_number >= observed_last_page
                    or not has_next
                ):
                    pagination_complete = True
                else:
                    pagination_complete = False
                break

            if page_number >= self.max_pages:
                pagination_complete = False
                break

            expected_page = page_number + 1
            click_result = self._click_pagination_next(
                page,
                expected_page=expected_page,
            )
            if not click_result.get("clicked"):
                pagination_complete = False
                page_meta.append(
                    {
                        "page_number": expected_page,
                        "click_result": click_result,
                        "rows": 0,
                        "stabilized": False,
                    }
                )
                break

            page.wait_for_timeout(self.wait_ms)
            try:
                page.wait_for_selector(
                    'a[href*="/ip/"]',
                    timeout=20_000,
                )
            except PlaywrightTimeoutError:
                pagination_complete = False
                page_meta.append(
                    {
                        "page_number": expected_page,
                        "click_result": click_result,
                        "rows": 0,
                        "stabilized": False,
                        "no_product_links": True,
                    }
                )
                break

            # Keep the low-resource/high-latency execution deliberately slow.
            page.wait_for_timeout(self.wait_ms)
            page_number = expected_page

        new_rows = [
            row
            for sku, row in unique_source.items()
            if sku not in seen_skus
        ]
        for row in new_rows:
            seen_skus.add(str(row.get("sku")))

        source_coverage = (
            len(unique_source) / source_target
            if source_target not in (None, 0)
            else None
        )

        if (
            observed_last_page is not None
            and observed_last_page > self.max_pages
        ):
            pagination_complete = False

        return new_rows, {
            "source_url": source_url,
            "actual_url": page.url,
            "rows": len(unique_source),
            "new_rows": len(new_rows),
            "target_products": source_target,
            "coverage": source_coverage,
            "coverage_threshold": 0.90,
            "product_links": max(
                [
                    int(item.get("product_links") or 0)
                    for item in page_meta
                ]
                or [0]
            ),
            "scroll_rounds": sum(
                int(item.get("pager_scroll_rounds") or 0)
                for item in page_meta
            ),
            "stabilized": bool(page_meta),
            "explicit_pages": max(0, len(page_meta) - 1),
            "explicit_pages_stabilized": pagination_complete,
            "pagination_complete": pagination_complete,
            "pages_visited": len(page_meta),
            "queued_pages_remaining": 0,
            "pagination_method": "ui_numeric_next",
            "pager_max_page": observed_last_page,
            "last_page_observed": observed_last_page,
            "pager_numbers": (
                page_meta[-1].get("pager_numbers")
                if page_meta
                else None
            ),
            "pager_control_numbers": (
                page_meta[-1].get("pager_control_numbers")
                if page_meta
                else None
            ),
            "pager_text_numbers": (
                page_meta[-1].get("pager_text_numbers")
                if page_meta
                else None
            ),
            "pager_text": (
                page_meta[-1].get("pager_text")
                if page_meta
                else None
            ),
            "sponsored_banner_clicks": 0,
            "explicit_page_meta": page_meta,
        }

    def _scrape_with_context(
        self,
        context: BrowserContext,
        category: Category,
        location: Location,
        *,
        establish_reference: bool,
        page: Page | None = None,
    ) -> list[dict[str, Any]]:
        """Scrape one Walmart business category from one or more browse sources.

        The site is paginated through the visible UI pager one page at a time.
        Page counts are dynamic and re-read after every navigation. max_pages
        is only a safety cap.
        """
        page = page or (
            context.pages[0] if context.pages else context.new_page()
        )

        explicit_sources = [
            str(url).strip()
            for url in getattr(category, "source_urls", ())
            if str(url).strip()
        ]
        sources = list(dict.fromkeys(explicit_sources or [category.url]))

        self.run_meta["source_strategy"] = (
            "configured_direct_browse_urls"
            if explicit_sources
            else "category_browse_url"
        )
        self.run_meta["sources_discovered"] = len(sources)
        self.run_meta["configured_source_urls"] = sources
        self.run_meta["sources"] = []
        self.run_meta["pages"] = []
        self.run_meta["sponsored_banner_clicks"] = 0

        rows: list[dict[str, Any]] = []
        seen_skus: set[str] = set()
        session_verified = not self.require_store_context
        verification_method: str | None = None

        if establish_reference:
            self._establish_store_reference(page, location)

        for source_index, source_url in enumerate(sources, start=1):
            response = page.goto(
                source_url,
                wait_until="domcontentloaded",
                timeout=120_000,
            )
            self._assert_not_blocked(
                page,
                response.status if response else None,
            )
            page.wait_for_timeout(self.wait_ms)

            try:
                page.wait_for_selector(
                    'a[href*="/ip/"]',
                    timeout=20_000,
                )
            except PlaywrightTimeoutError:
                self._save_diagnostics(
                    page,
                    f"no_products_{category.id}_source_{source_index}",
                )
                self.run_meta["sources"].append(
                    {
                        "source_index": source_index,
                        "source_url": source_url,
                        "rows": 0,
                        "new_rows": 0,
                        "pages_visited": 0,
                        "pagination_complete": False,
                        "pagination_method": "ui_numeric_next",
                        "no_product_links": True,
                    }
                )
                continue

            if not session_verified:
                session_verified, verification_method = (
                    self._verify_store_context(
                        page,
                        location,
                        allow_ui=True,
                        allow_product_detail=True,
                    )
                )
                if not session_verified:
                    self._save_diagnostics(
                        page,
                        f"store_context_missing_{category.id}",
                    )
                    raise WalmartStoreContextError(
                        "No se pudo probar el contexto de "
                        f"{location.store} / {location.postal_code}. "
                        "No se etiquetaron precios como datos de tienda."
                    )
                self._active_store_context_method = verification_method
                self.run_meta["store_context_verified"] = True
                self.run_meta["store_context_method"] = verification_method
            else:
                self._active_store_context_method = (
                    verification_method
                    or self._active_store_context_method
                    or "preverified"
                )

            source_target = self._target_count(page)
            source_seen: set[str] = set()
            source_page_meta: list[dict[str, Any]] = []
            page_number = 1
            observed_last_page: int | None = None
            pagination_complete = False
            click_result: dict[str, Any] = {
                "clicked": True,
                "method": "initial_page",
            }

            while page_number <= max(1, self.max_pages):
                self._assert_not_blocked(page)

                page_target = self._target_count(page)
                if page_target not in (None, 0):
                    source_target = max(
                        source_target or 0,
                        int(page_target),
                    )

                pager_snapshot = self._scroll_to_pagination(page)
                snapshot_last = pager_snapshot.get("max_page")
                try:
                    snapshot_last = (
                        int(snapshot_last)
                        if snapshot_last not in (None, "")
                        else None
                    )
                except (TypeError, ValueError):
                    snapshot_last = None

                if snapshot_last is not None and snapshot_last >= 1:
                    observed_last_page = max(
                        observed_last_page or 1,
                        snapshot_last,
                    )

                direct_verified, direct_method = (
                    self._verify_store_context(
                        page,
                        location,
                        allow_ui=False,
                        allow_product_detail=False,
                    )
                )
                page_method = (
                    direct_method
                    or verification_method
                    or self._active_store_context_method
                    or "preverified"
                )
                self._active_store_context_method = page_method

                page_rows = self._extract_cards(
                    page,
                    category,
                    location,
                )

                source_before = len(source_seen)
                category_before = len(seen_skus)
                for row in page_rows:
                    sku = str(row.get("sku") or "").strip()
                    if not sku:
                        continue
                    source_seen.add(sku)
                    if sku not in seen_skus:
                        seen_skus.add(sku)
                        rows.append(row)

                new_on_source_page = len(source_seen) - source_before
                new_on_category = len(seen_skus) - category_before

                pager_current = pager_snapshot.get("current")
                pager_proves_advance = bool(
                    pager_current is not None
                    and int(pager_current) == page_number
                )
                page_valid = bool(
                    page_number == 1
                    or new_on_source_page > 0
                    or pager_proves_advance
                )

                page_entry = {
                    "source_index": source_index,
                    "source_url": source_url,
                    "page": page_number,
                    "url": page.url,
                    "rows": len(page_rows),
                    "new_rows": new_on_category,
                    "new_source_rows": new_on_source_page,
                    "store_context_verified": True,
                    "store_context_method": page_method,
                    "direct_store_evidence": direct_verified,
                    "pager_current": pager_current,
                    "pager_proves_advance": pager_proves_advance,
                    "pager_max_page": snapshot_last,
                    "observed_last_page": observed_last_page,
                    "pager_numbers": pager_snapshot.get("numbers"),
                    "pager_control_numbers": pager_snapshot.get(
                        "control_numbers"
                    ),
                    "pager_text_numbers": pager_snapshot.get(
                        "text_numbers"
                    ),
                    "pager_text": pager_snapshot.get("text"),
                    "has_next": bool(
                        pager_snapshot.get("has_next")
                    ),
                    "next_enabled": bool(
                        pager_snapshot.get("next_enabled")
                    ),
                    "click_result": click_result,
                    "stabilized": page_valid,
                }
                self.run_meta["pages"].append(page_entry)
                source_page_meta.append(page_entry)

                page_display_total = (
                    observed_last_page
                    if observed_last_page is not None
                    else "?"
                )
                print(
                    "Walmart source="
                    f"{source_index}/{len(sources)} "
                    f"UI page {page_number}/{page_display_total}: "
                    f"rows={len(page_rows)}, "
                    f"new={new_on_source_page}, "
                    f"unique_source={len(source_seen)}, "
                    f"next_enabled="
                    f"{bool(pager_snapshot.get('next_enabled'))}",
                    flush=True,
                )

                if not page_valid:
                    pagination_complete = False
                    break

                next_enabled = bool(
                    pager_snapshot.get("next_enabled")
                )
                has_next = bool(
                    pager_snapshot.get("has_next")
                )

                if not next_enabled:
                    pagination_complete = bool(
                        observed_last_page is None
                        or page_number >= observed_last_page
                        or not has_next
                    )
                    break

                if page_number >= self.max_pages:
                    pagination_complete = False
                    break

                expected_page = page_number + 1
                click_result = self._click_pagination_next(
                    page,
                    expected_page=expected_page,
                )
                if not click_result.get("clicked"):
                    pagination_complete = False
                    source_page_meta.append(
                        {
                            "source_index": source_index,
                            "source_url": source_url,
                            "page": expected_page,
                            "rows": 0,
                            "click_result": click_result,
                            "stabilized": False,
                        }
                    )
                    break

                page.wait_for_timeout(self.wait_ms)
                try:
                    page.wait_for_selector(
                        'a[href*="/ip/"]',
                        timeout=20_000,
                    )
                except PlaywrightTimeoutError:
                    pagination_complete = False
                    source_page_meta.append(
                        {
                            "source_index": source_index,
                            "source_url": source_url,
                            "page": expected_page,
                            "rows": 0,
                            "click_result": click_result,
                            "stabilized": False,
                            "no_product_links": True,
                        }
                    )
                    break

                page.wait_for_timeout(self.wait_ms)
                page_number = expected_page

            source_coverage = (
                len(source_seen) / source_target
                if source_target not in (None, 0)
                else None
            )
            source_meta = {
                "source_index": source_index,
                "source_url": source_url,
                "rows": len(source_seen),
                "target_products": source_target,
                "coverage": source_coverage,
                "coverage_threshold": 0.90,
                "pages_visited": len(
                    [
                        item
                        for item in source_page_meta
                        if item.get("rows") is not None
                    ]
                ),
                "pagination_complete": pagination_complete,
                "pagination_method": "ui_numeric_next",
                "last_page_observed": observed_last_page,
                "sponsored_banner_clicks": 0,
                "page_meta": source_page_meta,
            }
            self.run_meta["sources"].append(source_meta)

            print(
                "Walmart source="
                f"{source_index}/{len(sources)}: "
                f"rows={source_meta['rows']}, "
                f"pages_visited={source_meta['pages_visited']}, "
                f"last_page_observed={observed_last_page}, "
                f"pagination_complete={pagination_complete}",
                flush=True,
            )

            # Additional breathing room between direct catalogue sources.
            page.wait_for_timeout(4_000)

        source_targets = [
            int(item["target_products"])
            for item in self.run_meta["sources"]
            if item.get("target_products") not in (None, 0)
        ]
        target_products = (
            sum(source_targets)
            if source_targets
            and len(source_targets) == len(self.run_meta["sources"])
            else None
        )
        coverage = (
            len(seen_skus) / target_products
            if target_products not in (None, 0)
            else None
        )
        coverage_ok = bool(
            coverage is not None and coverage >= 0.90
        )
        pagination_verified = bool(
            self.run_meta["sources"]
            and all(
                item.get("pagination_complete")
                for item in self.run_meta["sources"]
            )
        )

        self.run_meta["store_context_verified"] = session_verified
        self.run_meta["unique_products"] = len(seen_skus)
        self.run_meta["pagination_verified"] = pagination_verified
        self.run_meta["pagination_stop_reason"] = (
            "all_sources_complete"
            if pagination_verified
            else "source_pagination_incomplete"
        )
        self.run_meta["target_products"] = target_products
        self.run_meta["coverage"] = coverage
        self.run_meta["coverage_threshold"] = 0.90
        self.run_meta["coverage_ok"] = coverage_ok
        self.run_meta["pages_visited"] = len(self.run_meta["pages"])
        self._save_diagnostics(
            page,
            f"success_{category.id}_{location.id}",
        )
        return rows

    def scrape_category_on_page(
        self,
        page: Page,
        category: Category,
        location: Location,
    ) -> list[dict[str, Any]]:
        """Run Walmart on a page from the user's already-open Chrome.

        The caller owns the browser/context. This method never closes the
        manual Chrome session.
        """
        if self.require_store_context and (
            not location.store_id or not location.store
        ):
            raise WalmartStoreContextError(
                "Walmart requiere una tienda configurada para esta corrida."
            )

        self.run_meta = {
            "retailer": "Walmart",
            "category_id": category.id,
            "category_url": category.url,
            "browser_session_method": "existing_chrome_cdp",
        }
        self._active_store_context_method = None

        return self._scrape_with_context(
            page.context,
            category,
            location,
            establish_reference=False,
            page=page,
        )

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
