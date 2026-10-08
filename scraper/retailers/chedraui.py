from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from ..availability import AVAILABLE, UNAVAILABLE, UNKNOWN
from ..config import Category, Location
from ..parsers import absolute_url, clean_text, extract_sku, parse_money

BASE_URL = "https://www.chedraui.com.mx/"
PRODUCT_SELECTOR = 'a[href$="/p"], a[href*="/p?"]'
BLOCK_MARKERS = (
    "access denied",
    "forbidden",
    "captcha",
    "robot or human",
    "verifica tu identidad",
    "verify you are human",
)


class ChedrauiBlocked(RuntimeError):
    pass


class ChedrauiStoreContextError(RuntimeError):
    pass


class ChedrauiScraper:
    """Scraper browser-based para el catálogo público de Chedraui.

    Selecciona Pickup mediante la UI normal del sitio y sólo emite filas cuando
    el contexto de la tienda configurada se puede verificar. No resuelve ni
    evade CAPTCHAs, WAFs o desafíos de identidad.
    """

    def __init__(
        self,
        headless: bool = True,
        diagnostics_dir: str | Path = "diagnostics",
        max_pages: int = 100,
        wait_ms: int = 900,
        require_store_context: bool = True,
        browser_channel: str | None = None,
        profile_dir: str | Path | None = None,
        prepare_vtex_region: bool = True,
    ) -> None:
        self.headless = headless
        self.diagnostics_dir = Path(diagnostics_dir)
        self.diagnostics_dir.mkdir(parents=True, exist_ok=True)
        self.max_pages = max_pages
        self.wait_ms = wait_ms
        self.require_store_context = require_store_context
        self.browser_channel = browser_channel
        self.profile_dir = Path(profile_dir) if profile_dir else None
        if self.profile_dir is not None:
            self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.prepare_vtex_region = prepare_vtex_region
        self.run_meta: dict[str, Any] = {}
        self._active_store_context_method: str | None = None

    @staticmethod
    def _normalize(value: str | None) -> str:
        if not value:
            return ""
        value = value.lower()
        value = (
            value.replace("á", "a")
            .replace("é", "e")
            .replace("í", "i")
            .replace("ó", "o")
            .replace("ú", "u")
            .replace("ü", "u")
        )
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _paged_url(url: str, page_number: int) -> str:
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        if page_number <= 1:
            query.pop("page", None)
        else:
            query["page"] = str(page_number)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    def _save_diagnostics(self, page: Page, prefix: str) -> None:
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", prefix)
        try:
            page.screenshot(path=str(self.diagnostics_dir / f"chedraui_{safe}.png"), full_page=True)
        except Exception:
            pass
        try:
            (self.diagnostics_dir / f"chedraui_{safe}.html").write_text(page.content(), encoding="utf-8")
        except Exception:
            pass
        try:
            (self.diagnostics_dir / "chedraui_run_meta.json").write_text(
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
        if status in (401, 403, 429) or any(self._normalize(x) in body for x in BLOCK_MARKERS):
            self._save_diagnostics(page, "blocked")
            raise ChedrauiBlocked(
                "Chedraui bloqueó o desafió la sesión. Se guardaron diagnósticos; "
                "el scraper no intenta evadir la protección del sitio."
            )

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

    @classmethod
    def _store_context_in_text(cls, text: str, location: Location) -> bool:
        normalized = cls._normalize(text)
        store_id = cls._normalize(location.store_id)
        postal = cls._normalize(location.postal_code)
        polanco = "polanco" in normalized
        strong_detail = bool(
            (store_id and store_id in normalized)
            or (postal and postal in normalized)
            or "selecto mexico polanco" in normalized
        )
        pickup_detail = bool(
            re.search(r"(recoger|pickup|tienda).{0,140}polanco", normalized)
            or re.search(r"polanco.{0,140}(recoger|pickup|tienda)", normalized)
        )
        return polanco and (strong_detail or pickup_detail)

    @classmethod
    def _store_context_in_state_blob(cls, blob: str, location: Location) -> bool:
        normalized = cls._normalize(blob)
        hits = 0
        if "polanco" in normalized:
            hits += 1
        if location.store_id and cls._normalize(location.store_id) in normalized:
            hits += 1
        if location.postal_code and cls._normalize(location.postal_code) in normalized:
            hits += 1
        if "selecto" in normalized:
            hits += 1
        return hits >= 2 and "polanco" in normalized

    @classmethod
    def _orderform_matches_location(
        cls,
        payload: dict[str, Any] | None,
        location: Location,
    ) -> bool:
        if not isinstance(payload, dict):
            return False

        candidates: list[str] = []
        for key in ("storeId", "checkedInPickupPointId"):
            value = payload.get(key)
            if value is not None:
                candidates.append(str(value))

        shipping = payload.get("shippingData")
        if isinstance(shipping, dict):
            for info in shipping.get("logisticsInfo") or []:
                if not isinstance(info, dict):
                    continue
                for key in (
                    "selectedDeliveryChannel",
                    "pickupPointId",
                    "selectedSla",
                ):
                    value = info.get(key)
                    if value is not None:
                        candidates.append(str(value))

                pickup_info = info.get("pickupStoreInfo")
                if isinstance(pickup_info, dict):
                    for key in ("friendlyName", "additionalInfo", "dockId"):
                        value = pickup_info.get(key)
                        if value is not None:
                            candidates.append(str(value))
                    address = pickup_info.get("address")
                    if isinstance(address, dict):
                        for key in (
                            "addressName",
                            "street",
                            "neighborhood",
                            "city",
                            "postalCode",
                        ):
                            value = address.get(key)
                            if value is not None:
                                candidates.append(str(value))

                for sla in info.get("slas") or []:
                    if not isinstance(sla, dict):
                        continue
                    value = sla.get("pickupPointId")
                    if value is not None:
                        candidates.append(str(value))
                    store_info = sla.get("pickupStoreInfo")
                    if isinstance(store_info, dict):
                        for key in ("friendlyName", "additionalInfo", "dockId"):
                            value = store_info.get(key)
                            if value is not None:
                                candidates.append(str(value))
                        address = store_info.get("address")
                        if isinstance(address, dict):
                            for key in (
                                "addressName",
                                "street",
                                "neighborhood",
                                "city",
                                "postalCode",
                            ):
                                value = address.get(key)
                                if value is not None:
                                    candidates.append(str(value))

        normalized = cls._normalize(" ".join(candidates))
        if "polanco" not in normalized:
            return False

        postal = cls._normalize(location.postal_code)
        store_name = cls._normalize(location.store)
        strong = bool(
            (postal and postal in normalized)
            or (store_name and store_name in normalized)
            or "selecto mexico polanco" in normalized
        )
        return strong

    def _orderform_snapshot(self, page: Page) -> dict[str, Any] | None:
        try:
            response = page.context.request.get(
                BASE_URL + "api/checkout/pub/orderForm",
                timeout=30_000,
            )
            self.run_meta["orderform_status"] = response.status
            if response.status != 200:
                return None
            payload = response.json()
            if not isinstance(payload, dict):
                return None

            shipping = payload.get("shippingData")
            logistics = (
                shipping.get("logisticsInfo")
                if isinstance(shipping, dict)
                else None
            )
            self.run_meta["orderform_context"] = {
                "orderFormId": payload.get("orderFormId"),
                "storeId": payload.get("storeId"),
                "checkedInPickupPointId": payload.get("checkedInPickupPointId"),
                "logisticsInfo": logistics if isinstance(logistics, list) else [],
            }
            return payload
        except Exception as exc:
            self.run_meta["orderform_error"] = f"{type(exc).__name__}: {exc}"
            return None

    def _verify_store_context(self, page: Page, location: Location) -> tuple[bool, str | None]:
        if self._store_context_in_text(self._body_text(page), location):
            return True, "page_text"
        if self._store_context_in_state_blob(self._browser_state_blob(page), location):
            return True, "browser_state"

        orderform = self._orderform_snapshot(page)
        if self._orderform_matches_location(orderform, location):
            return True, "orderform"

        return False, None

    def _vtex_session_snapshot(self, page: Page) -> dict[str, Any] | None:
        """Lee el contexto regional público de la sesión VTEX."""
        try:
            response = page.context.request.get(
                BASE_URL + "api/sessions?items=public.country,public.postalCode,checkout.regionId",
                timeout=30_000,
            )
            if response.status != 200:
                self.run_meta["vtex_session_get_status"] = response.status
                return None
            payload = response.json()
            self.run_meta["vtex_session_get_status"] = response.status
            return payload if isinstance(payload, dict) else None
        except Exception as exc:
            self.run_meta["vtex_session_get_error"] = f"{type(exc).__name__}: {exc}"
            return None

    @classmethod
    def _pickup_matches_location(cls, item: dict, location: Location) -> bool:
        pickup = item.get("pickupPoint") if isinstance(item, dict) else None
        if not isinstance(pickup, dict):
            return False
        address = pickup.get("address") if isinstance(pickup.get("address"), dict) else {}
        blob = " ".join(
            str(value or "")
            for value in (
                pickup.get("friendlyName"),
                pickup.get("id"),
                pickup.get("name"),
                pickup.get("description"),
                address.get("addressName"),
                address.get("street"),
                address.get("neighborhood"),
                address.get("city"),
                address.get("postalCode"),
            )
        )
        normalized = cls._normalize(blob)
        store_name = cls._normalize(location.store)
        postal = cls._normalize(location.postal_code)

        if "polanco" not in normalized:
            return False
        if postal and postal not in normalized:
            # Algunos pickup points omiten CP en el nombre pero lo exponen en
            # address.postalCode. Si existe un CP distinto, no es Polanco.
            address_postal = cls._normalize(address.get("postalCode"))
            if address_postal and address_postal != postal:
                return False

        return bool(
            (store_name and store_name in normalized)
            or "selecto mexico polanco" in normalized
            or "chedraui" in normalized
        )

    def _list_pickup_points(self, page: Page, location: Location) -> list[dict]:
        postal = location.postal_code
        if not postal:
            return []
        query = urlencode({"postalCode": postal, "countryCode": "MEX"})
        try:
            response = page.context.request.get(
                BASE_URL + "api/checkout/pub/pickup-points?" + query,
                timeout=30_000,
            )
            self.run_meta["pickup_points_status"] = response.status
            if response.status != 200:
                return []
            payload = response.json()
            items = payload.get("items") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                return []
            candidates = [
                item for item in items
                if isinstance(item, dict) and self._pickup_matches_location(item, location)
            ]
            self.run_meta["pickup_points_total"] = len(items)
            self.run_meta["pickup_polanco_candidates"] = [
                {
                    "distance": item.get("distance"),
                    "pickupPoint": item.get("pickupPoint"),
                }
                for item in candidates[:10]
            ]
            return candidates
        except Exception as exc:
            self.run_meta["pickup_points_error"] = f"{type(exc).__name__}: {exc}"
            return []

    def _prepare_structured_store_context(
        self,
        page: Page,
        location: Location,
    ) -> tuple[bool, str | None]:
        """Regionaliza la sesión VTEX y confirma que Polanco existe como pickup.

        Esto no fuerza un pickupPoint en el carrito. Reduce la dependencia del
        modal visual al preparar postalCode/country y comprobar el directorio
        estructurado; la selección exacta de tienda sigue usando estado
        persistido o la UI normal del storefront.
        """
        if not self.prepare_vtex_region or not location.postal_code:
            return False, None

        snapshot = self._vtex_session_snapshot(page)
        token = snapshot.get("sessionToken") if isinstance(snapshot, dict) else None
        updated = False

        body = {
            "public": {
                "country": {"value": "MEX"},
                "postalCode": {"value": str(location.postal_code)},
            }
        }
        endpoint = (
            BASE_URL + "api/sessions/" + str(token)
            if token
            else BASE_URL + "api/sessions"
        )
        if not token:
            self.run_meta["vtex_session_token_missing"] = True

        try:
            response = page.context.request.post(
                endpoint,
                data=body,
                timeout=30_000,
            )
            self.run_meta["vtex_session_post_endpoint"] = endpoint
            self.run_meta["vtex_session_post_status"] = response.status
            updated = response.status in (200, 201, 204)
        except Exception as exc:
            self.run_meta["vtex_session_post_error"] = (
                f"{type(exc).__name__}: {exc}"
            )

        refreshed = self._vtex_session_snapshot(page)
        self.run_meta["vtex_session_updated"] = updated
        if isinstance(refreshed, dict):
            self.run_meta["vtex_session_snapshot"] = refreshed

        candidates = self._list_pickup_points(page, location)
        if candidates:
            return True, "vtex_session+pickup_points"
        return updated, "vtex_session" if updated else None

    @staticmethod
    def _click_text(page: Page, labels: tuple[str, ...], timeout: int = 5_000) -> bool:
        """Click the first visible, clickable text match across all candidates.

        VTEX can render duplicate hidden/mobile/desktop copies of the same
        label. Checking only the first match can miss the visible action in
        the address drawer (for example, "Recoger en" or "Aceptar").
        """
        for label in labels:
            try:
                nodes = page.get_by_text(label, exact=False)
                for i in range(min(nodes.count(), 40)):
                    node = nodes.nth(i)
                    try:
                        if not node.is_visible():
                            continue
                        node.click(timeout=timeout)
                        return True
                    except Exception:
                        continue
            except Exception:
                continue
        return False

    def _try_select_store_ui(self, page: Page, location: Location) -> tuple[bool, str | None]:
        # El banner de cookies puede cubrir el botón de ubicación.
        try:
            cookie = page.locator(".chedrauimx-frontend-applications-5-x-cookiesButtonAccept").first
            if cookie.count() and cookie.is_visible():
                cookie.click(timeout=4_000)
                page.wait_for_timeout(350)
        except Exception:
            self._click_text(page, ("Aceptar",), timeout=2_000)

        opened = False
        try:
            button = page.locator("button.chedrauimx-locator-2-x-labelTextAddress").first
            if button.count() and button.is_visible():
                button.click(timeout=5_000)
                opened = True
        except Exception:
            pass
        if not opened:
            opened = self._click_text(
                page,
                (
                    "Agregar una Dirección",
                    "Agregar una Direccion",
                    "Agregar dirección",
                    "Agrega dirección",
                ),
            )
        page.wait_for_timeout(700)
        self.run_meta["location_button_opened"] = opened
        if not opened:
            self._save_diagnostics(page, "store_location_button_not_found")
            return False, None

        # Modal VTEX: elegir Pickup.
        pickup_clicked = self._click_text(
            page,
            ("Recoger en una tienda", "Recoger en tienda", "Recoger en", "Pickup", "Recoger"),
        )
        page.wait_for_timeout(600)
        self.run_meta["pickup_clicked"] = pickup_clicked

        # El sitio solicita código postal para calcular los puntos de Pickup.
        postal_filled = False
        postal_value = location.postal_code or "11500"
        try:
            inputs = page.locator("input")
            for i in range(min(inputs.count(), 60)):
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
                    if any(x in hint for x in ("código postal", "codigo postal", "postal", "ubicación", "ubicacion")):
                        inp.fill(postal_value)
                        postal_filled = True
                        break
                except Exception:
                    continue
        except Exception:
            pass
        self.run_meta["postal_filled"] = postal_filled
        if postal_filled:
            self._click_text(page, ("Continuar", "Actualizar", "Buscar"), timeout=4_000)
            page.wait_for_timeout(1_200)

        # Selecciona específicamente Polanco si está entre los puntos de recogida.
        store_clicked = self._click_text(
            page,
            (
                location.store or "Chedraui Selecto México Polanco",
                "Selecto México Polanco",
                "Selecto Mexico Polanco",
                "México Polanco",
                "Mexico Polanco",
            ),
        )
        page.wait_for_timeout(700)
        self.run_meta["store_clicked"] = store_clicked

        if store_clicked:
            self._click_text(
                page,
                ("Seleccionar", "Elegir", "Usar esta tienda", "Confirmar", "Guardar", "Continuar"),
                timeout=4_000,
            )
            page.wait_for_timeout(1_000)

        verified, method = self._verify_store_context(page, location)
        if verified:
            return True, f"{method}_after_ui"

        # Diagnóstico intermedio del modal real para futuros cambios del storefront.
        self.run_meta["store_modal_text"] = self._body_text(page)[:8_000]
        self._save_diagnostics(page, "store_selection_unverified")
        return False, None

    @staticmethod
    def _infer_brand(product: str | None) -> str | None:
        if not product:
            return None
        known = [
            "Colgate", "Oral-B", "Listerine", "Sensodyne", "Crest", "GUM", "Corega",
            "Aquafresh", "Bexident", "Curaprox", "Philips", "Condor",
            "Suavitel", "Downy", "Ariel", "Ensueño", "Vanish", "Cloralex", "Ace",
            "MÁS", "Mas Color", "Roma", "Zote", "Persil", "Tide", "Dr. Beckmann",
            "Blanca Nieves", "Carisma", "Bold", "Oxiclean", "Arm & Hammer",
        ]
        lower = product.lower()
        for brand in known:
            if brand.lower() in lower:
                return brand
        return None

    @staticmethod
    def _availability_from_card_text(text: str | None) -> dict[str, Any]:
        raw = clean_text(text)
        if not raw:
            return {
                "availability_status": UNKNOWN,
                "is_available": None,
                "availability_raw": None,
            }

        negative = re.search(
            r"\b(agotad[oa]s?|sin\s+existencia|sin\s+stock|fuera\s+de\s+stock|out\s+of\s+stock)\b",
            raw,
            flags=re.IGNORECASE,
        )
        if negative:
            return {
                "availability_status": UNAVAILABLE,
                "is_available": False,
                "availability_raw": negative.group(0),
            }

        positive = re.search(
            r"\b(agregar(?:\s+al\s+carrito)?|añadir(?:\s+al\s+carrito)?|comprar\s+ahora)\b",
            raw,
            flags=re.IGNORECASE,
        )
        if positive:
            return {
                "availability_status": AVAILABLE,
                "is_available": True,
                "availability_raw": positive.group(0),
            }

        return {
            "availability_status": UNKNOWN,
            "is_available": None,
            "availability_raw": raw[:500],
        }

    def _extract_cards(self, page: Page, category: Category, location: Location) -> list[dict[str, Any]]:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        js = r"""
        anchors => {
          const money = /\$\s*[\d,.]+/;
          const unavailable = /Agotado|Sin existencia|Sin stock|Fuera de stock|Out of stock/i;
          const out = [];
          const seen = new Set();
          const pickText = (root, selectors) => {
            for (const sel of selectors) {
              const node = root.querySelector(sel);
              if (node && money.test(node.textContent || '')) return (node.textContent || '').trim();
            }
            return null;
          };
          for (const a of anchors) {
            const href = a.getAttribute('href');
            if (!href || seen.has(href) || !/\/p(?:\?|$)/i.test(href)) continue;
            seen.add(href);

            let card = a;
            for (let i = 0; i < 10 && card && card.parentElement; i++) {
              const txt = (card.textContent || '').replace(/\s+/g, ' ').trim();
              if ((money.test(txt) || unavailable.test(txt)) && txt.length > 20 && txt.length < 3000) break;
              card = card.parentElement;
            }
            if (!card) continue;
            const cardText = (card.textContent || '').replace(/\s+/g, ' ').trim();
            if (!money.test(cardText) && !unavailable.test(cardText)) continue;

            const img = card.querySelector('img[alt]');
            const heading = card.querySelector('h2, h3, [class*="productName"], [class*="product-name"]');
            const product =
              a.getAttribute('aria-label') ||
              a.getAttribute('title') ||
              (heading ? (heading.textContent || '').trim() : null) ||
              (img ? img.getAttribute('alt') : null) ||
              (a.textContent || '').trim();

            const selling = pickText(card, [
              '[class*="sellingPriceValue"]', '[class*="sellingPrice"]',
              '[class*="selling-price"]', '[data-testid*="selling"]'
            ]);
            const regular = pickText(card, [
              '[class*="listPriceValue"]', '[class*="listPrice"]',
              '[class*="list-price"]', '[data-testid*="list"]'
            ]);
            const priceNodes = Array.from(card.querySelectorAll('[class*="price"], [data-testid*="price"]'))
              .map(n => (n.textContent || '').trim())
              .filter(t => money.test(t));
            const promoNodes = Array.from(card.querySelectorAll('[class*="promo"], [class*="discount"], [class*="badge"]'))
              .map(n => (n.textContent || '').trim())
              .filter(Boolean);

            out.push({
              href,
              product,
              selling,
              regular,
              price_texts: [...new Set(priceNodes)],
              promo_texts: [...new Set(promoNodes)],
              card_text: cardText
            });
          }
          return out;
        }
        """
        locator = page.locator(PRODUCT_SELECTOR)
        if not locator.count():
            return []
        raw = locator.evaluate_all(js)
        rows: list[dict[str, Any]] = []
        for item in raw:
            url = absolute_url(item.get("href"), BASE_URL)
            product = clean_text(item.get("product"))
            if not product or not url:
                continue

            selling = parse_money(item.get("selling"))
            regular = parse_money(item.get("regular"))
            fallback_values: list[float] = []
            for text in item.get("price_texts") or [item.get("card_text")]:
                for token in re.findall(r"\$\s*[\d,]+(?:\.\d{1,2})?", text or ""):
                    value = parse_money(token)
                    if value is not None and value not in fallback_values:
                        fallback_values.append(value)

            current_price = selling if selling is not None else (fallback_values[0] if fallback_values else None)
            if regular is not None:
                regular_price = regular
            elif len(fallback_values) > 1:
                regular_price = fallback_values[1]
            else:
                regular_price = current_price
            if current_price and regular_price and current_price > regular_price:
                current_price, regular_price = regular_price, current_price

            availability = self._availability_from_card_text(
                item.get("card_text")
            )

            rows.append(
                {
                    "scrape_timestamp": now,
                    "retailer": "Chedraui",
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
                    "sku": extract_sku(url),
                    "brand": self._infer_brand(product),
                    "product": product,
                    "price_current": current_price,
                    "price_regular": regular_price,
                    "promotion": clean_text(" | ".join(item.get("promo_texts") or [])),
                    **availability,
                    "pickup_available": True,
                    "store_context_verified": True,
                    "store_context_method": self._active_store_context_method,
                    "url": url,
                    "price_raw": clean_text(" | ".join(item.get("price_texts") or [])),
                }
            )
        return rows

    def _collect_pages(self, page: Page, category: Category, location: Location) -> list[dict[str, Any]]:
        rows_by_key: dict[str, dict[str, Any]] = {}
        stale_pages = 0
        max_pages = self.max_pages if self.max_pages > 0 else 100

        for page_number in range(1, max_pages + 1):
            url = self._paged_url(category.url, page_number)
            response = page.goto(url, wait_until="domcontentloaded", timeout=120_000)
            self._assert_not_blocked(page, response.status if response else None)
            page.wait_for_timeout(self.wait_ms)
            try:
                page.wait_for_selector(PRODUCT_SELECTOR, timeout=20_000)
            except PlaywrightTimeoutError:
                if page_number == 1:
                    self._save_diagnostics(page, f"no_products_{category.id}")
                break

            page_rows = self._extract_cards(page, category, location)
            before = len(rows_by_key)
            for row in page_rows:
                key = str(row.get("sku") or row.get("url"))
                if key:
                    rows_by_key[key] = row
            new_count = len(rows_by_key) - before
            self.run_meta.setdefault("pages", []).append(
                {"page": page_number, "url": page.url, "rows": len(page_rows), "new": new_count}
            )

            if new_count == 0:
                stale_pages += 1
            else:
                stale_pages = 0
            if stale_pages >= 2:
                break

        return list(rows_by_key.values())

    def scrape_category(self, category: Category, location: Location) -> list[dict[str, Any]]:
        if self.require_store_context and (not location.store_id or not location.store):
            raise ChedrauiStoreContextError(
                "Chedraui requiere una tienda configurada para esta corrida."
            )

        with sync_playwright() as p:
            launch_kwargs = {"headless": self.headless}
            if self.browser_channel:
                launch_kwargs["channel"] = self.browser_channel

            browser = None
            if self.profile_dir is not None:
                context: BrowserContext = p.chromium.launch_persistent_context(
                    user_data_dir=str(self.profile_dir),
                    locale="es-MX",
                    viewport={"width": 1440, "height": 1000},
                    **launch_kwargs,
                )
                page = context.pages[0] if context.pages else context.new_page()
            else:
                browser = p.chromium.launch(**launch_kwargs)
                context = browser.new_context(
                    locale="es-MX",
                    viewport={"width": 1440, "height": 1000},
                )
                page = context.new_page()

            try:
                # Cargar el origen primero permite que VTEX cree/recupere
                # vtex_session y vtex_segment antes de regionalizar.
                response = page.goto(
                    BASE_URL,
                    wait_until="domcontentloaded",
                    timeout=120_000,
                )
                self._assert_not_blocked(page, response.status if response else None)
                page.wait_for_timeout(max(self.wait_ms, 900))

                structured_ok, structured_method = (
                    self._prepare_structured_store_context(page, location)
                )
                self.run_meta["structured_store_context_ready"] = structured_ok
                self.run_meta["structured_store_context_method"] = structured_method

                response = page.goto(
                    category.url,
                    wait_until="domcontentloaded",
                    timeout=120_000,
                )
                self._assert_not_blocked(page, response.status if response else None)
                page.wait_for_timeout(1_500)

                verified, method = self._verify_store_context(page, location)
                if not verified:
                    verified, method = self._try_select_store_ui(page, location)

                if self.require_store_context and not verified:
                    self.run_meta["store_context_verified"] = False
                    self._save_diagnostics(page, "store_context_error")
                    raise ChedrauiStoreContextError(
                        "No fue posible verificar Chedraui Selecto México Polanco (tienda 232)."
                    )

                self._active_store_context_method = (
                    method
                    or structured_method
                    or "persistent_profile"
                )
                self.run_meta["store_context_verified"] = True
                self.run_meta["store_context_method"] = self._active_store_context_method
                self.run_meta["store_id"] = location.store_id
                self.run_meta["store"] = location.store

                rows = self._collect_pages(page, category, location)
                self._save_diagnostics(page, f"success_{category.id}")
                return rows
            finally:
                try:
                    if not page.is_closed():
                        page.close(run_before_unload=False)
                except Exception:
                    pass
                try:
                    context.close()
                except Exception:
                    pass
                if browser is not None:
                    try:
                        browser.close()
                    except Exception:
                        pass
