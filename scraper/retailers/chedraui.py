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
STORE_CONTEXT_CONTRACT_VERSION = "chedraui-directory-ui-v3"
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
        low_memory: bool = False,
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
        self.low_memory = low_memory
        self.run_meta: dict[str, Any] = {}
        self._active_store_context_method: str | None = None

    @staticmethod
    def _low_memory_browser_args() -> list[str]:
        return [
            "--disable-gpu",
            "--disable-extensions",
            "--disable-sync",
            "--disable-translate",
            "--no-first-run",
            "--no-default-browser-check",
            "--renderer-process-limit=2",
        ]

    @staticmethod
    def _install_low_memory_routes(context: BrowserContext) -> None:
        def handle(route, request):
            if request.resource_type in {"image", "media", "font"}:
                route.abort()
            else:
                route.continue_()

        context.route("**/*", handle)

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

    @classmethod
    def _location_store_tokens(cls, location: Location) -> list[str]:
        normalized = cls._normalize(location.store)
        generic = {
            "chedraui", "selecto", "super", "supermercado",
            "mexico", "tienda",
        }
        return [
            token
            for token in re.findall(r"[a-z0-9]+", normalized)
            if len(token) >= 3 and token not in generic
        ]

    @staticmethod
    def _expected_pickup_value(
        location: Location,
    ) -> str | None:
        """Build VTEX pickup-point value from the configured Chedraui store id."""
        if not location.store_id:
            return None
        raw = str(location.store_id).strip()
        if not raw.isdigit():
            return None
        numeric = int(raw)
        return f"chedrauimx{numeric:04d}_PP{numeric:04d}"

    @classmethod
    def _location_store_id_matches_blob(
        cls,
        blob: str,
        location: Location,
    ) -> bool:
        if not location.store_id:
            return False

        raw = str(location.store_id).strip()
        text = str(blob)

        if not raw.isdigit():
            return cls._normalize(raw) in cls._normalize(text)

        numeric = str(int(raw))
        padded = rf"0*{re.escape(numeric)}"

        patterns = (
            rf"(?i)chedrauimx{padded}(?:[_-]?PP{padded})?",
            rf"(?i)\\bPP{padded}\\b",
            rf"(?i)(?:storeId|store_id)[^0-9]{{0,12}}[\"']?{padded}[\"']?",
            rf"(?i)(?:seller|pickup|dock)[^0-9]{{0,12}}[\"']?{padded}[\"']?",
        )
        return any(re.search(pattern, text) for pattern in patterns)

    @classmethod
    def _location_name_matches(
        cls,
        blob: str,
        location: Location,
    ) -> bool:
        normalized = cls._normalize(blob)
        full_name = cls._normalize(location.store)
        if full_name and full_name in normalized:
            return True

        tokens = cls._location_store_tokens(location)
        if not tokens:
            return False

        # Store labels can omit format words such as "Selecto" or "México".
        # Require all meaningful tokens to avoid matching another Chedraui in
        # the same city/postal area.
        return all(token in normalized for token in tokens)

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

    def _visible_store_control_text(self, page: Page) -> str:
        selectors = (
            "button.chedrauimx-locator-2-x-labelTextAddress",
            '[class*="locator"][class*="labelTextAddress"]',
            'button[aria-label*="tienda" i]',
            'button[aria-label*="ubicaci" i]',
            '[data-testid*="store" i]',
            '[data-testid*="location" i]',
        )
        values: list[str] = []
        for selector in selectors:
            try:
                nodes = page.locator(selector)
                for index in range(min(nodes.count(), 12)):
                    node = nodes.nth(index)
                    try:
                        if not node.is_visible():
                            continue
                        text = clean_text(
                            node.inner_text(timeout=1_500)
                        )
                        if text:
                            values.append(text)
                        aria = clean_text(
                            node.get_attribute("aria-label")
                        )
                        if aria:
                            values.append(aria)
                    except Exception:
                        continue
            except Exception:
                continue
        return " | ".join(dict.fromkeys(values))

    @classmethod
    def _store_control_matches_location(
        cls,
        text: str,
        location: Location,
    ) -> bool:
        if not text:
            return False
        normalized = cls._normalize(text)
        if not normalized:
            return False
        if cls._location_name_matches(normalized, location):
            return True

        tokens = cls._location_store_tokens(location)
        return bool(
            tokens
            and all(token in normalized for token in tokens)
        )

    @classmethod
    def _store_context_in_text(cls, text: str, location: Location) -> bool:
        normalized = cls._normalize(text)
        if not normalized:
            return False

        name_match = cls._location_name_matches(normalized, location)
        postal = cls._normalize(location.postal_code)
        postal_match = bool(postal and postal in normalized)
        pickup_context = bool(
            re.search(r"(recoger|pickup|tienda).{0,180}", normalized)
        )

        return name_match and (postal_match or pickup_context)


    @classmethod
    def _store_context_in_state_blob(
        cls,
        blob: str,
        location: Location,
    ) -> bool:
        normalized = cls._normalize(blob)
        if not normalized:
            return False

        name_match = cls._location_name_matches(normalized, location)
        postal = cls._normalize(location.postal_code)
        postal_match = bool(postal and postal in normalized)

        store_id_match = False
        if location.store_id:
            escaped = re.escape(str(location.store_id))
            store_id_match = bool(
                re.search(
                    rf'(?i)(?:storeid|store_id|seller|pickup)[^0-9]{{0,12}}["\']?{escaped}["\']?',
                    blob,
                )
            )

        return name_match and (store_id_match or postal_match)


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

        if location.store_id:
            root_store_id = payload.get("storeId")
            if root_store_id is not None and str(root_store_id) == str(location.store_id):
                return True

        name_match = cls._location_name_matches(normalized, location)
        postal = cls._normalize(location.postal_code)
        postal_match = bool(postal and postal in normalized)
        return name_match and postal_match

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

    def _verify_store_context(
        self,
        page: Page,
        location: Location,
    ) -> tuple[bool, str | None]:
        control_text = self._visible_store_control_text(page)
        self.run_meta["visible_store_control_text"] = control_text
        if self._store_control_matches_location(
            control_text,
            location,
        ):
            return True, "visible_store_control"

        state_blob = self._browser_state_blob(page)
        if self._store_context_in_state_blob(
            state_blob,
            location,
        ):
            return True, "browser_state"

        orderform = self._orderform_snapshot(page)
        if self._orderform_matches_location(
            orderform,
            location,
        ):
            return True, "orderform"

        # Whole-page text is intentionally diagnostic only. The store name can
        # appear in hidden directory/modal content without governing the VTEX
        # segment used by productSearchV3.
        self.run_meta["page_text_store_hint"] = (
            self._store_context_in_text(
                self._body_text(page),
                location,
            )
        )
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
    def _pickup_matches_location(
        cls,
        item: dict,
        location: Location,
    ) -> bool:
        pickup = item.get("pickupPoint") if isinstance(item, dict) else None
        if not isinstance(pickup, dict):
            return False

        address = (
            pickup.get("address")
            if isinstance(pickup.get("address"), dict)
            else {}
        )
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

        name_match = cls._location_name_matches(blob, location)
        store_id_match = cls._location_store_id_matches_blob(
            blob,
            location,
        )

        # The VTEX pickup technical store ID is authoritative. A store can
        # legitimately expose a pickup postal code different from the postal
        # code used to regionalize the storefront session.
        if store_id_match:
            return True

        expected_postal = cls._normalize(location.postal_code)
        actual_postal = cls._normalize(address.get("postalCode"))
        if expected_postal and actual_postal and actual_postal != expected_postal:
            return False

        return name_match


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
            self.run_meta["pickup_points_preview"] = [
                {
                    "distance": item.get("distance"),
                    "pickupPoint": item.get("pickupPoint"),
                }
                for item in items[:20]
            ]
            self.run_meta["pickup_store_candidates"] = [
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
        """Regionaliza la sesión VTEX y confirma que la tienda existe como pickup.

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

    def _establish_store_before_category(
        self,
        page: Page,
        location: Location,
    ) -> tuple[bool, str | None]:
        """Select the exact store through the visible directory every time.

        Production deliberately does not trust inherited profile/session
        context here. The deterministic route is:
        locator -> pickup -> store directory -> state -> city/zone ->
        configured store -> confirmation -> visible-store verification.
        """
        self.run_meta["store_selection_strategy"] = (
            "directory_ui_state_city_store"
        )
        self.run_meta["vtex_region_mutation_skipped"] = True
        self.run_meta["preexisting_store_context_ignored"] = True

        verified, method = self._try_select_store_ui(
            page,
            location,
        )
        if not verified:
            self.run_meta["directory_ui_store_verified"] = False
            return False, None

        required_steps = {
            "location_button_opened": bool(
                self.run_meta.get("location_button_opened")
            ),
            # Some Chedraui layouts expose "Directorio de tiendas" directly
            # from the locator, so the intermediate Pickup click is diagnostic
            # rather than mandatory when the directory was reached.
            "directory_clicked": bool(
                self.run_meta.get("directory_clicked")
            ),
            "state_selected": bool(
                self.run_meta.get("state_selected")
            ),
            "city_selected": bool(
                self.run_meta.get("city_selected")
            ),
            "store_clicked": bool(
                self.run_meta.get("store_clicked")
            ),
            "pickup_confirmed": bool(
                self.run_meta.get("pickup_confirmed")
            ),
        }
        self.run_meta["directory_ui_steps"] = required_steps

        if not all(required_steps.values()):
            self.run_meta["directory_ui_store_verified"] = False
            self.run_meta["directory_selection_failure"] = (
                "incomplete_directory_ui_steps"
            )
            self._save_diagnostics(
                page,
                "store_directory_incomplete_steps",
            )
            return False, None

        # The visible store control must show the configured store after the
        # directory has closed. This is the final UI-side proof.
        control_text = self._visible_store_control_text(page)
        self.run_meta["visible_store_control_text"] = control_text
        control_verified = self._store_control_matches_location(
            control_text,
            location,
        )
        self.run_meta["directory_ui_store_verified"] = (
            bool(control_verified)
        )
        if not control_verified:
            self.run_meta["directory_selection_failure"] = (
                "visible_store_control_mismatch"
            )
            self._save_diagnostics(
                page,
                "store_directory_visible_control_mismatch",
            )
            return False, None

        snapshot = self._vtex_session_snapshot(page)
        if isinstance(snapshot, dict):
            self.run_meta["vtex_session_after_store_selection"] = (
                snapshot
            )

        return True, (
            method
            or "visible_store_control_after_directory_ui"
        )

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

        # Selecciona la tienda configurada. El directorio puede omitir palabras
        # de formato como "Selecto" o "México", por lo que además del nombre
        # completo se prueban los tokens significativos.
        store_tokens = self._location_store_tokens(location)
        token_label = " ".join(store_tokens)
        short_label = " ".join(store_tokens[-2:]) if store_tokens else ""

        pickup_candidates = self._list_pickup_points(page, location)
        pickup_labels: list[str] = []
        for item in pickup_candidates[:10]:
            pickup = item.get("pickupPoint") if isinstance(item, dict) else None
            if not isinstance(pickup, dict):
                continue
            for key in ("friendlyName", "name", "description", "id"):
                value = pickup.get(key)
                if value:
                    pickup_labels.append(str(value))

        labels = tuple(
            dict.fromkeys(
                value
                for value in (
                    location.store,
                    *pickup_labels,
                    token_label,
                    short_label,
                    store_tokens[-1] if store_tokens else None,
                )
                if value
            )
        )
        self.run_meta["store_selection_labels"] = list(labels)
        store_clicked = self._click_text(page, labels)
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
            if self.low_memory:
                launch_kwargs["args"] = self._low_memory_browser_args()

            browser = None
            if self.profile_dir is not None:
                context: BrowserContext = p.chromium.launch_persistent_context(
                    user_data_dir=str(self.profile_dir),
                    locale="es-MX",
                    viewport={"width": 1440, "height": 1000},
                    **launch_kwargs,
                )
                if self.low_memory:
                    self._install_low_memory_routes(context)
                page = context.pages[0] if context.pages else context.new_page()
            else:
                browser = p.chromium.launch(**launch_kwargs)
                context = browser.new_context(
                    locale="es-MX",
                    viewport={"width": 1440, "height": 1000},
                )
                if self.low_memory:
                    self._install_low_memory_routes(context)
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
                page.wait_for_timeout(max(self.wait_ms, 2_000))

                verified, method = (
                    self._establish_store_before_category(
                        page,
                        location,
                    )
                )

                if self.require_store_context and not verified:
                    self.run_meta["store_context_verified"] = False
                    self._save_diagnostics(
                        page,
                        "store_context_pre_category_error",
                    )
                    raise ChedrauiStoreContextError(
                        f"No fue posible seleccionar y verificar "
                        f"{location.store} (tienda {location.store_id}) "
                        "antes de abrir la categoría."
                    )

                response = page.goto(
                    category.url,
                    wait_until="domcontentloaded",
                    timeout=120_000,
                )
                self._assert_not_blocked(
                    page,
                    response.status if response else None,
                )
                page.wait_for_timeout(3_000)
                try:
                    page.wait_for_selector(
                        PRODUCT_SELECTOR,
                        state="attached",
                        timeout=30_000,
                    )
                except Exception:
                    pass

                category_verified, category_method = (
                    self._verify_store_context(
                        page,
                        location,
                    )
                )
                if not category_verified:
                    category_verified, category_method = (
                        self._try_select_store_ui(
                            page,
                            location,
                        )
                    )
                    if category_verified:
                        # Exact store selection can change vtex_segment. Reload
                        # the category once so productSearchV3 runs under the
                        # newly selected store segment.
                        response = page.goto(
                            category.url,
                            wait_until="domcontentloaded",
                            timeout=120_000,
                        )
                        self._assert_not_blocked(
                            page,
                            response.status if response else None,
                        )
                        page.wait_for_timeout(2_500)

                if (
                    self.require_store_context
                    and not category_verified
                ):
                    self.run_meta["store_context_verified"] = False
                    self._save_diagnostics(
                        page,
                        "store_context_error",
                    )
                    raise ChedrauiStoreContextError(
                        f"No fue posible mantener {location.store} "
                        f"(tienda {location.store_id}) en la categoría."
                    )

                method = category_method or method
                self._active_store_context_method = (
                    method
                    or "persistent_profile"
                )
                self.run_meta["store_context_verified"] = True
                self.run_meta["store_context_method"] = self._active_store_context_method
                self.run_meta["store_context_contract_version"] = (
                    STORE_CONTEXT_CONTRACT_VERSION
                )
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
