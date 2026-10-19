from __future__ import annotations

import math
import os
import re
from datetime import datetime
from pathlib import Path

from .chedraui import (
    PRODUCT_SELECTOR,
    ChedrauiBlocked,
    ChedrauiScraper as BaseChedrauiScraper,
    ChedrauiStoreContextError,
)
from ..config import Location


class ChedrauiScraper(BaseChedrauiScraper):
    """Chedraui scraper using one live VTEX directory flow for every store."""

    def _trace_directory_step(self, page, location: Location, step: str) -> None:
        """Optional screenshots for troubleshooting visible UI; never alter state."""
        if os.environ.get("CHEDRAUI_TRACE_STORE", "").strip() != "1":
            return
        try:
            destination = (
                Path(self.diagnostics_dir) / "chedraui_store_steps"
            )
            destination.mkdir(parents=True, exist_ok=True)
            label = re.sub(r"[^a-zA-Z0-9_-]", "_", str(step))[:60]
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = destination / (
                f"store{location.store_id}_{timestamp}_{label}.png"
            )
            page.screenshot(
                path=str(path), full_page=False, timeout=10_000,
            )
            self.run_meta.setdefault("directory_ui_screenshots", []).append(
                str(path)
            )
            print(f"CHEDRAUI UI TRACE [{step}]: {path}", flush=True)
        except Exception as exc:
            self.run_meta.setdefault("directory_ui_trace_errors", []).append(
                f"{step}: {type(exc).__name__}: {exc}"
            )

    def _accept_cookies(self, page) -> bool:
        clicked = False
        try:
            cookie = page.locator(
                ".chedrauimx-frontend-applications-5-x-cookiesButtonAccept"
            ).first
            if cookie.count() and cookie.is_visible():
                cookie.click(timeout=4_000)
                clicked = True
        except Exception:
            pass
        if not clicked:
            clicked = self._click_text(page, ("Aceptar",), timeout=4_000)
        page.wait_for_timeout(800)
        return clicked

    def _open_locator(self, page) -> bool:
        """Open a *visible* store locator; tolerate delayed VTEX header hydration.

        Never treat an inherited profile store as proof. This must actually
        open the locator/directory in the current page session.
        """
        selectors = (
            "button.chedrauimx-locator-2-x-labelTextAddress",
            'button[class*="locator"][class*="labelTextAddress"]',
            '[class*="locator"][class*="labelTextAddress"]',
            'button[aria-label*="tienda" i]',
            'button[aria-label*="ubicaci" i]',
            '[data-testid*="location" i]',
            '[data-testid*="store" i]',
        )
        attempts = []
        for attempt in range(10):
            # The home page's VTEX header can hydrate after DOMContentLoaded.
            # Use short bounded waits, not sleeps without useful evidence.
            snapshot = {
                "attempt": attempt + 1,
                "url": getattr(page, "url", None),
                "selectors": {},
                "errors": [],
            }
            for selector in selectors:
                try:
                    nodes = page.locator(selector)
                    count = nodes.count()
                    snapshot["selectors"][selector] = count
                except Exception as exc:
                    snapshot["errors"].append(
                        f"{selector}: {type(exc).__name__}: {str(exc)[:180]}"
                    )
                    continue

                for index in range(min(count, 15)):
                    try:
                        button = nodes.nth(index)
                        if not button.is_visible():
                            continue
                        label = self._normalize(
                            (button.inner_text(timeout=1_500) or "")
                            + " "
                            + (button.get_attribute("aria-label") or "")
                        )
                        snapshot.setdefault("visible_controls", []).append(
                            {"selector": selector, "index": index,
                             "text": label[:300]}
                        )
                        button.click(timeout=4_000)
                        page.wait_for_timeout(700)
                        self.run_meta["location_button_match_index"] = index
                        self.run_meta["location_button_selector"] = selector
                        self.run_meta["location_button_visible_text"] = label
                        self.run_meta["location_locator_attempts"] = attempts + [snapshot]
                        return True
                    except Exception as exc:
                        snapshot["errors"].append(
                            f"{selector}[{index}]: {type(exc).__name__}: "
                            + str(exc)[:180]
                        )
            attempts.append(snapshot)
            if attempt < 9:
                page.wait_for_timeout(900)

        # With no configured store yet the header sometimes renders an
        # address link rather than the VTEX locator button.
        opened = self._click_text(
            page,
            (
                "Agregar una Dirección",
                "Agregar una Direccion",
                "Agregar dirección",
                "Agregar una",
                "Cambiar tienda",
                "Seleccionar tienda",
                "Elige tu tienda",
                "Mi tienda",
            ),
            timeout=4_000,
        )
        if opened:
            page.wait_for_timeout(700)
        self.run_meta["location_button_matches"] = attempts[-1]["selectors"].get(
            selectors[0], 0
        )
        self.run_meta["location_locator_attempts"] = attempts
        self.run_meta["location_locator_fallback_clicked"] = bool(opened)
        self.run_meta["location_locator_final_url"] = getattr(page, "url", None)
        return bool(opened)

    def _click_enabled_pickup_button(self, page) -> bool:
        try:
            labels = page.get_by_text("Recoger en esta tienda", exact=False)
            for i in range(min(labels.count(), 30)):
                label = labels.nth(i)
                if not label.is_visible():
                    continue
                button = label.locator("xpath=ancestor::button[1]")
                if button.count() and button.is_enabled():
                    button.click(timeout=5_000)
                    return True
        except Exception:
            pass
        return False

    def _click_polanco(self, page) -> bool:
        patterns = (
            "Chedraui Selecto México Polanco",
            "CHEDRAUI SELECTO MEXICO POLANCO",
            "Selecto México Polanco",
            "Selecto Mexico Polanco",
            "México Polanco",
            "Mexico Polanco",
            "Polanco",
        )
        for label in patterns:
            try:
                nodes = page.get_by_text(label, exact=False)
                for i in range(min(nodes.count(), 60)):
                    node = nodes.nth(i)
                    if not node.is_visible():
                        continue
                    text = self._normalize(node.inner_text(timeout=1_500))
                    if "polanco" not in text:
                        continue
                    node.click(timeout=5_000)
                    page.wait_for_timeout(500)
                    return True
            except Exception:
                continue
        return False

    def _select_directory_option(
        self,
        select,
        preferred_tokens: tuple[str, ...],
    ) -> tuple[bool, list[str], str | None]:
        try:
            options = [
                value.strip()
                for value in select.locator("option").all_text_contents()
                if value.strip()
            ]
        except Exception:
            options = []

        def match_key(value: str) -> str:
            return re.sub(
                r"[^a-z0-9]+",
                " ",
                self._normalize(value),
            ).strip()

        normalized_tokens = [
            match_key(value)
            for value in preferred_tokens
            if value
        ]

        preferred = None
        for token in normalized_tokens:
            if not token:
                continue
            preferred = next(
                (
                    value
                    for value in options
                    if token in match_key(value)
                ),
                None,
            )
            if preferred:
                break
        if not preferred:
            return False, options, None

        try:
            select.select_option(label=preferred)
            return True, options, preferred
        except Exception:
            return False, options, preferred

    def _select_directory_option_with_retry(
        self,
        page,
        select_index: int,
        preferred_tokens: tuple[str, ...],
        *,
        attempts: int = 8,
        wait_ms: int = 750,
    ) -> tuple[bool, list[str], str | None]:
        last_options: list[str] = []
        last_selected: str | None = None

        for _ in range(attempts):
            try:
                selects = page.locator(
                    "select.chedrauimx-locator-2-x-selectOption"
                )
                if selects.count() <= select_index:
                    page.wait_for_timeout(wait_ms)
                    continue

                ok, options, selected = self._select_directory_option(
                    selects.nth(select_index),
                    preferred_tokens,
                )
                last_options = options
                last_selected = selected
                if ok:
                    return True, options, selected
            except Exception:
                pass

            try:
                page.wait_for_timeout(wait_ms)
            except Exception:
                break

        return False, last_options, last_selected


    def _click_store_radio_by_id(
        self,
        page,
        location: Location,
    ) -> bool:
        """Click the visible label for the exact VTEX pickup radio by store id."""
        expected = self._expected_pickup_value(location)
        self.run_meta["directory_store_expected_pickup_value"] = expected
        if not expected:
            return False

        try:
            radios = page.locator(
                'input[name="pickup-point-list"]'
                '[type="radio"]'
                f'[value="{expected}"]'
            )
            count = radios.count()
        except Exception:
            count = 0

        self.run_meta["directory_store_radio_matches"] = count
        tokens = self._location_store_tokens(location)

        for index in range(min(count, 20)):
            radio = radios.nth(index)
            try:
                radio_id = radio.get_attribute("id")
            except Exception:
                radio_id = None
            if not radio_id:
                continue

            try:
                labels = page.locator(
                    f'label[for="{radio_id}"]'
                )
            except Exception:
                continue

            for label_index in range(min(labels.count(), 8)):
                label = labels.nth(label_index)
                try:
                    if not label.is_visible():
                        continue
                    label_text = self._normalize(
                        label.inner_text(timeout=1_500)
                    )
                except Exception:
                    continue

                # Guard against a duplicated VTEX radio accidentally pointing
                # to another rendered card.
                if tokens and not all(
                    token in label_text for token in tokens
                ):
                    continue

                try:
                    label.click(timeout=5_000)
                    page.wait_for_timeout(700)
                except Exception:
                    continue

                verified = False
                try:
                    refreshed = page.locator(
                        'input[name="pickup-point-list"]'
                        '[type="radio"]'
                        f'[value="{expected}"]'
                    )
                    for selected_index in range(
                        min(refreshed.count(), 20)
                    ):
                        try:
                            if refreshed.nth(
                                selected_index
                            ).is_checked():
                                verified = True
                                break
                        except Exception:
                            continue
                except Exception:
                    verified = False

                self.run_meta["directory_store_radio_id"] = radio_id
                self.run_meta["directory_store_radio_label"] = (
                    label_text[:1_000]
                )
                self.run_meta[
                    "directory_store_radio_verified"
                ] = verified

                if verified:
                    self.run_meta[
                        "directory_store_match_method"
                    ] = "exact_store_id_radio"
                    return True

        return False

    def _click_configured_store(
        self,
        page,
        location: Location,
        extra_labels: tuple[str, ...] = (),
    ) -> bool:
        """Select the exact store card, even when its name spans child nodes."""
        tokens = self._location_store_tokens(location)
        labels = tuple(
            dict.fromkeys(
                value
                for value in (
                    location.store,
                    *extra_labels,
                    *tokens,
                )
                if value
            )
        )
        self.run_meta["directory_store_labels"] = list(labels)
        self.run_meta["directory_store_tokens"] = list(tokens)

        # Primary production path: Chedraui exposes the store id in the VTEX
        # radio value (for example store 255 -> chedrauimx0255_PP0255).
        # Click the visible label associated with that exact radio and verify
        # the native input became checked. Text/card matching remains fallback.
        if self._click_store_radio_by_id(page, location):
            return True

        # First prefer an exact/full-name text node when the storefront exposes
        # one. This is the cheapest and least ambiguous path.
        for label in labels:
            try:
                nodes = page.get_by_text(label, exact=False)
                for i in range(min(nodes.count(), 80)):
                    node = nodes.nth(i)
                    try:
                        if not node.is_visible():
                            continue
                        text = self._normalize(
                            node.inner_text(timeout=1_500)
                        )
                        if tokens and not all(
                            token in text for token in tokens
                        ):
                            continue
                        node.click(timeout=5_000)
                        page.wait_for_timeout(600)
                        self.run_meta[
                            "directory_store_match_method"
                        ] = "direct_text"
                        return True
                    except Exception:
                        continue
            except Exception:
                continue

        if not tokens:
            return False

        # Store cards commonly split the store name across several descendants.
        # Start from the rarest/specific token and climb a few ancestors until
        # a compact visible container contains every meaningful store token.
        probe_tokens = list(reversed(tokens))
        seen_containers: set[str] = set()

        for probe in probe_tokens:
            try:
                nodes = page.get_by_text(probe, exact=False)
            except Exception:
                continue

            for i in range(min(nodes.count(), 100)):
                try:
                    node = nodes.nth(i)
                    if not node.is_visible():
                        continue
                except Exception:
                    continue

                current = node
                for depth in range(0, 7):
                    try:
                        text = self._normalize(
                            current.inner_text(timeout=1_500)
                        )
                    except Exception:
                        text = ""

                    if (
                        text
                        and len(text) <= 1_800
                        and all(token in text for token in tokens)
                    ):
                        signature = text[:500]
                        if signature in seen_containers:
                            break
                        seen_containers.add(signature)

                        self.run_meta[
                            "directory_store_matched_text"
                        ] = text[:1_000]
                        self.run_meta[
                            "directory_store_match_depth"
                        ] = depth

                        # Prefer a radio/label/card selector inside the matched
                        # store container. Do not click a generic confirmation
                        # button here; confirmation remains a separate gate.
                        candidate_selectors = (
                            'input[type="radio"]',
                            '[role="radio"]',
                            'label',
                            '[role="option"]',
                            '[tabindex="0"]',
                        )
                        for selector in candidate_selectors:
                            try:
                                controls = current.locator(selector)
                                for j in range(
                                    min(controls.count(), 12)
                                ):
                                    control = controls.nth(j)
                                    if not control.is_visible():
                                        continue
                                    control.click(timeout=5_000)
                                    page.wait_for_timeout(600)
                                    self.run_meta[
                                        "directory_store_match_method"
                                    ] = (
                                        "ancestor_card_control:"
                                        + selector
                                    )
                                    return True
                            except Exception:
                                continue

                        # Some Chedraui store cards are themselves clickable.
                        try:
                            current.click(timeout=5_000)
                            page.wait_for_timeout(600)
                            self.run_meta[
                                "directory_store_match_method"
                            ] = "ancestor_card"
                            return True
                        except Exception:
                            pass

                    if depth >= 6:
                        break
                    try:
                        parent = current.locator("xpath=..")
                        if not parent.count():
                            break
                        current = parent.first
                    except Exception:
                        break

        self.run_meta["directory_store_match_method"] = (
            "no_matching_store_card"
        )
        return False

    @staticmethod
    def _directory_selects_visible(page) -> bool:
        """Only real, visible State/City dropdowns prove the directory opened."""
        try:
            nodes = page.locator(
                "select.chedrauimx-locator-2-x-selectOption"
            )
            return any(
                nodes.nth(index).is_visible()
                for index in range(min(nodes.count(), 5))
            )
        except Exception:
            return False

    def _open_pickup_directory(self, page) -> tuple[bool, bool]:
        """Choose pickup and open directory, waiting for asynchronously drawn UI.

        Do not click the header's broad 'Recoger en: <store>' text again:
        it can toggle/close the locator instead of opening the pickup tab.
        Never fabricate a successful step without a visible directory.
        """
        observations: list[dict] = []
        pickup_clicked = False
        directory_clicked = False
        directory_method: str | None = None
        pickup_labels = (
            "Recoger en una tienda",
            "Recoger en tienda",
            "Recoger en",
        )

        for attempt in range(12):
            observation: dict = {
                "attempt": attempt + 1,
                "directory_selects_visible": (
                    self._directory_selects_visible(page)
                ),
                "directory_text_matches": 0,
                "directory_text_visible": 0,
                "pickup_visible_labels": [],
                "errors": [],
            }

            if observation["directory_selects_visible"]:
                directory_clicked = True
                directory_method = (
                    directory_method or "visible_directory_controls"
                )
                observations.append(observation)
                break

            # Prefer an explicit directory action over a generic pickup text.
            # After a successful click, poll for the dropdowns without
            # clicking the link repeatedly while VTEX is still rendering.
            if not directory_clicked:
                try:
                    nodes = page.get_by_text("Directorio de tiendas", exact=False)
                    observation["directory_text_matches"] = nodes.count()
                    for index in range(min(nodes.count(), 25)):
                        item = nodes.nth(index)
                        if not item.is_visible():
                            continue
                        label = self._normalize(
                            item.inner_text(timeout=1_000)
                        )
                        if not re.fullmatch(
                            r"directorio de tiendas(?:\\s*[›»>])?",
                            label,
                        ):
                            continue
                        observation["directory_text_visible"] += 1
                        try:
                            item.click(timeout=3_000)
                            directory_clicked = True
                            directory_method = "visible_directory_text"
                            observation["directory_clicked"] = True
                            page.wait_for_timeout(700)
                            break
                        except Exception as exc:
                            observation["errors"].append(
                                "directory_click:"
                                + type(exc).__name__ + ": " + str(exc)[:100]
                            )
                except Exception as exc:
                    observation["errors"].append(
                        "directory_find:" + type(exc).__name__
                    )

            if directory_clicked and self._directory_selects_visible(page):
                observation["directory_selects_visible"] = True
                observations.append(observation)
                break

            # A compact, exact pickup tab/action is safer than the prior broad
            # get_by_text('Recoger en', exact=False), which may match the
            # selected-store address header and close the drawer.
            if not pickup_clicked and attempt in (1, 4, 8):
                for label in pickup_labels:
                    try:
                        nodes = page.get_by_text(label, exact=True)
                        for index in range(min(nodes.count(), 15)):
                            node = nodes.nth(index)
                            if not node.is_visible():
                                continue
                            observation["pickup_visible_labels"].append(label)
                            try:
                                node.click(timeout=3_000)
                                pickup_clicked = True
                                observation["pickup_clicked_label"] = label
                                page.wait_for_timeout(700)
                                break
                            except Exception as exc:
                                observation["errors"].append(
                                    "pickup_click:" + type(exc).__name__
                                    + ": " + str(exc)[:100]
                                )
                        if pickup_clicked:
                            break
                    except Exception as exc:
                        observation["errors"].append(
                            "pickup_find:" + type(exc).__name__
                        )

            observations.append(observation)
            if attempt < 11:
                page.wait_for_timeout(750)

        final_visible = self._directory_selects_visible(page)
        self.run_meta["pickup_clicked"] = pickup_clicked
        self.run_meta["directory_clicked"] = (
            bool(directory_clicked) and bool(final_visible)
        )
        self.run_meta["directory_open_method"] = directory_method
        self.run_meta["directory_final_selects_visible"] = final_visible
        self.run_meta["directory_open_observations"] = observations
        if not final_visible:
            self.run_meta["directory_selection_failure"] = (
                "directory_controls_not_visible"
            )
        return pickup_clicked, bool(directory_clicked and final_visible)

    def _try_select_store_directory_generic(
        self,
        page,
        location: Location,
    ):
        self.run_meta["cookie_clicked"] = self._accept_cookies(page)

        opened = self._open_locator(page)
        self.run_meta["location_button_opened"] = opened
        self._trace_directory_step(page, location, "01_locator_abierto")
        if not opened:
            self._save_diagnostics(page, "store_location_button_not_found")
            return False, None

        pickup_clicked, directory_clicked = (
            self._open_pickup_directory(page)
        )
        self._trace_directory_step(page, location, "02_opcion_recoger")
        self._trace_directory_step(page, location, "03_directorio")
        if not directory_clicked:
            self._save_diagnostics(page, "store_directory_not_found")
            return False, None
        page.wait_for_timeout(900)

        pickup_candidates = self._list_pickup_points(page, location)
        pickup_names: list[str] = []
        pickup_cities: list[str] = []
        pickup_states: list[str] = []
        for item in pickup_candidates[:10]:
            pickup = item.get("pickupPoint") if isinstance(item, dict) else None
            if not isinstance(pickup, dict):
                continue
            name = pickup.get("friendlyName")
            if name:
                pickup_names.append(str(name))
            address = pickup.get("address")
            if isinstance(address, dict):
                city = address.get("city")
                state = address.get("state")
                if city:
                    pickup_cities.append(str(city))
                if state:
                    pickup_states.append(str(state))

        state_selected = False
        city_selected = False
        state_options: list[str] = []
        city_options: list[str] = []
        state_label = None
        city_label = None

        try:
            state_selected, state_options, state_label = (
                self._select_directory_option_with_retry(
                    page,
                    0,
                    (
                        location.state,
                        *pickup_states,
                    ),
                    attempts=4,
                    wait_ms=500,
                )
            )
            if state_selected:
                page.wait_for_timeout(1_000)
            self._trace_directory_step(page, location, "04_estado")

            city_selected, city_options, city_label = (
                self._select_directory_option_with_retry(
                    page,
                    1,
                    (
                        location.directory_area,
                        *pickup_cities,
                        location.city,
                        *self._location_store_tokens(location),
                    ),
                    attempts=10,
                    wait_ms=800,
                )
            )
            if city_selected:
                page.wait_for_timeout(1_400)
            self._trace_directory_step(page, location, "05_ciudad_zona")
        except Exception as exc:
            self.run_meta["directory_select_error"] = str(exc)

        self.run_meta["state_selected"] = state_selected
        self.run_meta["city_selected"] = city_selected
        self.run_meta["state_options"] = state_options[:80]
        self.run_meta["city_options"] = city_options[:80]
        self.run_meta["selected_state_label"] = state_label
        self.run_meta["selected_city_label"] = city_label
        self.run_meta["configured_directory_area"] = location.directory_area
        self.run_meta["pickup_candidate_names"] = pickup_names[:10]

        # Production contract: the directory route must explicitly resolve
        # State -> City/Zone before a store can be selected. Never skip these
        # steps based on stale browser/session state.
        if not state_selected:
            self.run_meta["directory_selection_failure"] = (
                "state_not_selected"
            )
            self._save_diagnostics(
                page,
                "store_directory_state_not_selected",
            )
            return False, None

        if not city_selected:
            self.run_meta["directory_selection_failure"] = (
                "city_not_selected"
            )
            self._save_diagnostics(
                page,
                "store_directory_city_not_selected",
            )
            return False, None

        store_clicked = self._click_configured_store(
            page,
            location,
            tuple(pickup_names),
        )
        self.run_meta["store_clicked"] = store_clicked
        page.wait_for_timeout(700)
        self._trace_directory_step(page, location, "06_tienda_seleccionada")

        confirmed = (
            self._click_enabled_pickup_button(page)
            if store_clicked
            else False
        )
        if not confirmed and store_clicked:
            confirmed = self._click_text(
                page,
                (
                    "Seleccionar",
                    "Elegir",
                    "Recoger en esta tienda",
                    "Confirmar",
                    "Guardar",
                    "Continuar",
                ),
                timeout=4_000,
            )
        self.run_meta["pickup_confirmed"] = confirmed
        page.wait_for_timeout(1_500)
        self._trace_directory_step(page, location, "07_tienda_confirmada")

        if not store_clicked:
            self.run_meta["directory_selection_failure"] = (
                "configured_store_not_clicked"
            )
            self._save_diagnostics(
                page,
                "store_directory_store_not_clicked",
            )
            return False, None

        if not confirmed:
            self.run_meta["directory_selection_failure"] = (
                "pickup_not_confirmed"
            )
            self._save_diagnostics(
                page,
                "store_directory_pickup_not_confirmed",
            )
            return False, None

        # The site's selected-store header can update asynchronously after
        # the confirmation drawer closes. Poll the *visible* header briefly;
        # never interpret having clicked a store card as proof of selection.
        header_observations: list[dict] = []
        header_matched = False
        for attempt in range(12):
            visible = self._visible_store_control_text(page)
            matched = self._store_control_matches_location(visible, location)
            header_observations.append({
                "attempt": attempt + 1,
                "visible_store_control_text": visible[:400],
                "matches_configured_store": bool(matched),
            })
            if matched:
                header_matched = True
                break
            if attempt < 11:
                page.wait_for_timeout(750)
        self.run_meta["directory_header_observations"] = header_observations
        self.run_meta["directory_header_matched_after_confirmation"] = (
            header_matched
        )

        # Keep the normal independent verification. Even a matching visible
        # label will be cross-checked by the Phase-1 directory UI contract.
        verified, method = self._verify_store_context(page, location)
        self.run_meta["directory_final_verification_method"] = method
        self._trace_directory_step(
            page, location,
            "08_contexto_verificado" if verified else "08_contexto_no_verificado",
        )
        if verified:
            return True, f"{method}_after_directory_ui"

        self.run_meta["directory_selection_failure"] = (
            "store_not_proven_after_confirmation"
        )
        self.run_meta["store_modal_text"] = self._body_text(page)[:14_000]
        self._save_diagnostics(page, "store_directory_selection_unverified")
        return False, None

    def _try_select_store_ui(self, page, location: Location):
        # One canonical store-selection flow for every Chedraui location.
        # Store-specific behavior comes only from Location + live VTEX
        # pickup metadata, never from a separate branch per store.
        return self._try_select_store_directory_generic(
            page,
            location,
        )

    @staticmethod
    def _infer_brand(product: str | None) -> str | None:
        """Use phrase boundaries so short brands do not match inside words."""
        if not product:
            return None
        brands = [
            "Arm & Hammer", "Dr. Beckmann", "Blanca Nieves", "Mas Color",
            "Oral-B", "Sensodyne", "Listerine", "Aquafresh", "Bexident",
            "Curaprox", "Colgate", "Philips", "Corega", "Cloralex", "Oxiclean",
            "Suavitel", "Ensueño", "Vanish", "Princesa", "Downy", "Persil",
            "Ariel", "Carisma", "Condor", "Crest", "Tide", "Bold", "Zote",
            "Roma", "MÁS", "Ace", "GUM",
        ]
        normalized = product.casefold()
        for brand in sorted(brands, key=len, reverse=True):
            pattern = rf"(?<!\w){re.escape(brand.casefold())}(?!\w)"
            if re.search(pattern, normalized):
                return brand
        return None

    def _pagination_snapshot(self, page) -> dict:
        """Read Chedraui's live paginator: Mostrando X-Y de Z Productos."""
        text = self._body_text(page)
        match = re.search(
            r"Mostrando\s+([\d,]+)\s*[-–]\s*([\d,]+)"
            r"\s+de\s+([\d,]+)\s+Productos",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return {
                "start": None,
                "end": None,
                "total": None,
                "page_size": getattr(
                    self,
                    "_chedraui_paginator_page_size",
                    None,
                ),
                "current_page": None,
                "total_pages": None,
                "next_available": self._paginator_next_available(
                    page
                ),
            }

        start = int(match.group(1).replace(",", ""))
        end = int(match.group(2).replace(",", ""))
        total = int(match.group(3).replace(",", ""))

        page_size = getattr(
            self,
            "_chedraui_paginator_page_size",
            None,
        )
        observed_size = max(end - start + 1, 1)

        # The first page defines the stable shelf size. Never replace it with
        # the shorter final-page range.
        if start == 1 and observed_size > 0:
            page_size = observed_size
            self._chedraui_paginator_page_size = page_size
        elif not page_size:
            page_size = observed_size

        current_page = (
            ((start - 1) // page_size) + 1
            if page_size
            else None
        )
        total_pages = (
            math.ceil(total / page_size)
            if page_size and total > 0
            else 0
        )

        return {
            "start": start,
            "end": end,
            "total": total,
            "page_size": page_size,
            "current_page": current_page,
            "total_pages": total_pages,
            "next_available": self._paginator_next_available(page),
        }

    def _paginator_next_control(self, page):
        """Return the visible native Siguiente control when present."""
        try:
            labels = page.get_by_text(
                re.compile(r"^\s*Siguiente\s*$", re.IGNORECASE)
            )
            for index in range(min(labels.count(), 12)):
                label = labels.nth(index)
                try:
                    if not label.is_visible():
                        continue

                    tag = (
                        label.evaluate(
                            "el => el.tagName.toLowerCase()"
                        )
                        or ""
                    )
                    control = label
                    if tag not in ("a", "button"):
                        parent = label.locator(
                            "xpath=ancestor::*[self::a or self::button][1]"
                        )
                        if parent.count():
                            control = parent.first

                    aria_disabled = (
                        control.get_attribute("aria-disabled")
                        or ""
                    ).strip().lower()
                    disabled = (
                        aria_disabled == "true"
                        or control.get_attribute("disabled")
                        is not None
                    )
                    classes = (
                        control.get_attribute("class") or ""
                    ).casefold()
                    if "disabled" in classes:
                        disabled = True

                    return control, not disabled
                except Exception:
                    continue
        except Exception:
            pass
        return None, False

    def _paginator_next_available(self, page) -> bool:
        _, available = self._paginator_next_control(page)
        return available

    def _click_paginator_next(
        self,
        page,
        *,
        expected_page: int,
    ) -> tuple[bool, dict]:
        """Advance one real UI page and prove that the displayed range moved."""
        before = self._pagination_snapshot(page)
        control, available = self._paginator_next_control(page)
        info = {
            "mode": "ui_next",
            "expected_page": expected_page,
            "before": before,
            "clicked": False,
            "advanced": False,
        }
        if control is None or not available:
            info["reason"] = "next_not_available"
            return False, info

        try:
            control.scroll_into_view_if_needed(timeout=3_000)
        except Exception:
            try:
                page.evaluate(
                    "window.scrollTo(0, document.body.scrollHeight)"
                )
            except Exception:
                pass

        try:
            control.click(timeout=7_000)
            info["clicked"] = True
        except Exception as exc:
            info["reason"] = (
                f"next_click:{type(exc).__name__}"
            )
            return False, info

        previous_start = before.get("start")
        for _ in range(12):
            try:
                page.wait_for_timeout(500)
            except Exception:
                pass
            after = self._pagination_snapshot(page)
            after_page = after.get("current_page")
            after_start = after.get("start")
            moved = (
                after_page == expected_page
                or (
                    previous_start is not None
                    and after_start is not None
                    and after_start > previous_start
                )
            )
            if moved:
                info["after"] = after
                info["advanced"] = True
                return True, info

        info["after"] = self._pagination_snapshot(page)
        info["reason"] = "range_did_not_advance"
        return False, info

    def _position_on_paginator_page(
        self,
        page,
        category,
        page_number: int,
    ) -> tuple[bool, list[dict]]:
        """Reach page N sequentially through Chedraui's native paginator."""
        attempts: list[dict] = []

        snapshot = self._pagination_snapshot(page)
        current = snapshot.get("current_page")

        # Category navigation always begins on page 1. If diagnostics cannot
        # read the first paginator yet, give the catalogue a final settle.
        if page_number == 1:
            if current in (None, 1):
                return True, attempts
            response = page.goto(
                category.url,
                wait_until="domcontentloaded",
                timeout=90_000,
            )
            self._assert_not_blocked(
                page,
                response.status if response else None,
            )
            self._wait_for_catalog_ready(
                page,
                selector_timeout=35_000,
                settle_ms=max(self.wait_ms, 2_500),
            )
            snapshot = self._pagination_snapshot(page)
            return (
                snapshot.get("current_page") in (None, 1),
                attempts,
            )

        if current is None:
            attempts.append(
                {
                    "mode": "ui_next",
                    "expected_page": page_number,
                    "reason": "current_page_unknown",
                }
            )
            return False, attempts

        if current > page_number:
            attempts.append(
                {
                    "mode": "ui_next",
                    "expected_page": page_number,
                    "reason": (
                        f"current_page_ahead:{current}"
                    ),
                }
            )
            return False, attempts

        while current < page_number:
            ok, info = self._click_paginator_next(
                page,
                expected_page=current + 1,
            )
            attempts.append(info)
            if not ok:
                return False, attempts
            current = (
                info.get("after", {}).get("current_page")
                or current + 1
            )

        return current == page_number, attempts

    def _store_specific_total(self, page) -> int | None:
        """Read the category total from Chedraui's live paginator."""
        snapshot = self._pagination_snapshot(page)
        total = snapshot.get("total")
        if total is not None:
            return int(total)

        # Compatibility fallback if Chedraui temporarily omits the
        # "Mostrando X-Y de Z" prefix but still publishes "Z Productos".
        text = self._body_text(page)
        match = re.search(
            r"\b([\d,]+)\s+Productos\b",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        try:
            return int(match.group(1).replace(",", ""))
        except ValueError:
            return None

    def _wait_for_catalog_ready(
        self,
        page,
        *,
        selector_timeout: int = 30_000,
        settle_ms: int = 2_500,
    ) -> bool:
        """Wait until the async VTEX product grid is present and stable."""
        try:
            page.wait_for_timeout(settle_ms)
        except Exception:
            pass

        try:
            page.wait_for_selector(
                PRODUCT_SELECTOR,
                state="attached",
                timeout=selector_timeout,
            )
        except Exception:
            return False

        previous_count = -1
        stable_rounds = 0
        for _ in range(6):
            try:
                count = page.locator(PRODUCT_SELECTOR).count()
            except Exception:
                return previous_count > 0

            if count > 0 and count == previous_count:
                stable_rounds += 1
            else:
                stable_rounds = 0

            previous_count = count
            if stable_rounds >= 2:
                return True

            try:
                page.wait_for_timeout(750)
            except Exception:
                break

        return previous_count > 0

    def _scroll_grid(self, page) -> None:
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1_200)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(700)
        except Exception:
            pass

    def _rows_from_current_page(self, page, category, location) -> list[dict]:
        if not self._wait_for_catalog_ready(page):
            return []
        self._scroll_grid(page)
        try:
            page.wait_for_timeout(750)
        except Exception:
            pass
        return self._extract_cards(page, category, location)

    def _recover_page_from_previous(self, page, category, location, page_number: int) -> tuple[list[dict], dict]:
        """Fallback for VTEX pages that fail when opened directly.

        Opens the previous valid page and follows Chedraui's own paginator link,
        preserving the normal storefront routing and store context.
        """
        info: dict = {"mode": "previous_page_click", "rows": 0}
        if page_number <= 1:
            return [], info

        previous_url = self._paged_url(category.url, page_number - 1)
        try:
            response = page.goto(previous_url, wait_until="domcontentloaded", timeout=90_000)
            self._assert_not_blocked(page, response.status if response else None)
            self._wait_for_catalog_ready(
                page,
                selector_timeout=35_000,
                settle_ms=max(self.wait_ms, 2_500),
            )
            previous_rows = self._rows_from_current_page(page, category, location)
            info["previous_rows"] = len(previous_rows)
            if not previous_rows:
                info["reason"] = "previous_page_empty"
                return [], info

            links = page.locator(f'a[href*="page={page_number}"]')
            clicked = False
            for i in range(min(links.count(), 20)):
                link = links.nth(i)
                try:
                    if link.is_visible():
                        link.click(timeout=5_000)
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked and links.count():
                try:
                    links.first.evaluate("el => el.click()")
                    clicked = True
                except Exception:
                    pass
            info["clicked"] = clicked
            if not clicked:
                info["reason"] = "paginator_link_not_found"
                return [], info

            page.wait_for_timeout(2_500)
            self._wait_for_catalog_ready(
                page,
                selector_timeout=35_000,
                settle_ms=1_500,
            )
            rows = self._rows_from_current_page(page, category, location)
            info["rows"] = len(rows)
            return rows, info
        except Exception as exc:
            info["reason"] = f"fallback_error:{type(exc).__name__}"
            return [], info

    def _load_page_rows(
        self,
        page,
        category,
        location,
        page_number: int,
        target_rows: int | None,
    ):
        """Read one page after reaching it through Chedraui's native UI."""
        positioned, attempts = self._position_on_paginator_page(
            page,
            category,
            page_number,
        )
        if not positioned:
            self._save_diagnostics(
                page,
                f"pagination_ui_position_failed_{page_number}",
            )
            return page.url, [], attempts

        best: dict[str, dict] = {}
        for attempt in range(1, 5):
            try:
                ready = self._wait_for_catalog_ready(
                    page,
                    selector_timeout=35_000,
                    settle_ms=(
                        1_500
                        if attempt == 1
                        else min(1_500 * attempt, 4_500)
                    ),
                )
                rows = (
                    self._rows_from_current_page(
                        page,
                        category,
                        location,
                    )
                    if ready
                    else []
                )
            except Exception as exc:
                attempts.append(
                    {
                        "attempt": attempt,
                        "rows": 0,
                        "reason": (
                            f"catalog_read:{type(exc).__name__}"
                        ),
                    }
                )
                continue

            for row in rows:
                key = str(row.get("sku") or row.get("url"))
                if key:
                    best[key] = row

            attempts.append(
                {
                    "attempt": attempt,
                    "rows": len(rows),
                    "best": len(best),
                    "catalog_ready": bool(rows),
                    "paginator": self._pagination_snapshot(page),
                }
            )

            if target_rows is None:
                if best:
                    break
            elif len(best) >= target_rows:
                break

        if not best:
            self._save_diagnostics(
                page,
                f"pagination_empty_page_{page_number}",
            )

        return page.url, list(best.values()), attempts

    def _collect_pages(self, page, category, location):
        rows_by_key: dict[str, dict] = {}
        max_pages = self.max_pages if self.max_pages > 0 else 100
        self._chedraui_paginator_page_size = None

        initial = self._pagination_snapshot(page)
        displayed_total = initial.get("total")
        page_size = initial.get("page_size")
        dynamic_total_pages = initial.get("total_pages")

        self.run_meta["pagination_method"] = (
            "ui_next_dynamic_range"
        )
        self.run_meta["pagination_initial_snapshot"] = initial
        self.run_meta["displayed_category_products"] = (
            displayed_total
        )
        self.run_meta["page_size"] = page_size
        self.run_meta["displayed_expected_pages"] = (
            dynamic_total_pages
        )

        if not page_size:
            # Let page 1 render fully once, then derive the category-specific
            # shelf size from the live "Mostrando X-Y de Z" range.
            self._wait_for_catalog_ready(
                page,
                selector_timeout=35_000,
                settle_ms=max(self.wait_ms, 2_500),
            )
            initial = self._pagination_snapshot(page)
            displayed_total = initial.get("total")
            page_size = initial.get("page_size")
            dynamic_total_pages = initial.get("total_pages")
            self.run_meta["pagination_initial_snapshot"] = initial
            self.run_meta["displayed_category_products"] = (
                displayed_total
            )
            self.run_meta["page_size"] = page_size
            self.run_meta["displayed_expected_pages"] = (
                dynamic_total_pages
            )

        page_number = 1
        internal_gaps: list[int] = []

        while page_number <= max_pages:
            before_snapshot = self._pagination_snapshot(page)

            # Re-read the total on every page. Catalogue totals can vary by
            # category/store and can even change during a long run.
            live_total = before_snapshot.get("total")
            live_total_pages = before_snapshot.get("total_pages")
            if live_total is not None:
                displayed_total = live_total
                self.run_meta["displayed_category_products"] = (
                    displayed_total
                )
            if live_total_pages is not None:
                dynamic_total_pages = live_total_pages
                self.run_meta["displayed_expected_pages"] = (
                    dynamic_total_pages
                )

            if (
                dynamic_total_pages is not None
                and dynamic_total_pages > 0
                and page_number > dynamic_total_pages
            ):
                break

            if page_size and displayed_total:
                if (
                    dynamic_total_pages
                    and page_number < dynamic_total_pages
                ):
                    target_rows = page_size
                elif (
                    dynamic_total_pages
                    and page_number == dynamic_total_pages
                ):
                    target_rows = max(
                        displayed_total
                        - page_size * (dynamic_total_pages - 1),
                        1,
                    )
                else:
                    target_rows = None
            else:
                target_rows = None

            url, page_rows, attempts = self._load_page_rows(
                page,
                category,
                location,
                page_number,
                target_rows,
            )

            after_snapshot = self._pagination_snapshot(page)
            live_total = after_snapshot.get("total")
            live_total_pages = after_snapshot.get("total_pages")
            if live_total is not None:
                displayed_total = live_total
                self.run_meta["displayed_category_products"] = (
                    displayed_total
                )
            if live_total_pages is not None:
                dynamic_total_pages = live_total_pages
                self.run_meta["displayed_expected_pages"] = (
                    dynamic_total_pages
                )
            if after_snapshot.get("page_size"):
                page_size = after_snapshot["page_size"]
                self.run_meta["page_size"] = page_size

            before = len(rows_by_key)
            for row in page_rows:
                key = str(row.get("sku") or row.get("url"))
                if key:
                    rows_by_key[key] = row
            new_count = len(rows_by_key) - before

            if not page_rows:
                internal_gaps.append(page_number)

            self.run_meta.setdefault("pages", []).append(
                {
                    "page": page_number,
                    "url": page.url or url,
                    "rows": len(page_rows),
                    "new": new_count,
                    "paginator_before": before_snapshot,
                    "paginator_after": after_snapshot,
                    "attempts": attempts,
                }
            )

            on_last_page = (
                dynamic_total_pages is not None
                and dynamic_total_pages > 0
                and page_number >= dynamic_total_pages
            )
            no_next = not after_snapshot.get("next_available")

            if on_last_page:
                break

            # If the UI says there should be another page, absence of
            # "Siguiente" is a structural pagination failure, not normal EOF.
            if no_next:
                internal_gaps.append(page_number + 1)
                self.run_meta[
                    "pagination_terminated_without_next"
                ] = {
                    "page": page_number,
                    "expected_pages": dynamic_total_pages,
                    "snapshot": after_snapshot,
                }
                break

            page_number += 1

        self.run_meta["collected_products"] = len(rows_by_key)
        self.run_meta["internal_pagination_gaps"] = sorted(
            set(internal_gaps)
        )
        self.run_meta["listing_exhausted"] = (
            not internal_gaps
            and (
                dynamic_total_pages in (None, 0)
                or page_number >= dynamic_total_pages
            )
        )
        self.run_meta["pagination_final_snapshot"] = (
            self._pagination_snapshot(page)
        )
        if displayed_total is not None:
            self.run_meta["displayed_count_coverage"] = (
                round(
                    len(rows_by_key) / displayed_total,
                    4,
                )
                if displayed_total
                else None
            )

        if page_number >= max_pages:
            final_snapshot = self._pagination_snapshot(page)
            final_pages = final_snapshot.get("total_pages")
            if final_pages and final_pages > max_pages:
                self.run_meta["pagination_safety_limit_hit"] = True
                internal_gaps.append(max_pages + 1)

        if internal_gaps:
            self.run_meta["internal_pagination_gaps"] = sorted(
                set(internal_gaps)
            )
            self._save_diagnostics(
                page,
                "pagination_internal_gap",
            )
            raise RuntimeError(
                "Chedraui no pudo demostrar continuidad del "
                f"paginador UI: {sorted(set(internal_gaps))}"
            )

        return list(rows_by_key.values())


__all__ = [
    "ChedrauiBlocked",
    "ChedrauiStoreContextError",
    "ChedrauiScraper",
]
