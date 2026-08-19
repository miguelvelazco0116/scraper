from __future__ import annotations

import re

from .chedraui import (
    ChedrauiBlocked,
    ChedrauiScraper as BaseChedrauiScraper,
    ChedrauiStoreContextError,
)
from ..config import Location


class ChedrauiScraper(BaseChedrauiScraper):
    """Chedraui scraper with the live VTEX address flow used by Polanco."""

    POLANCO_ADDRESS = (
        "Av. Miguel de Cervantes Saavedra 397, Irrigación, "
        "Miguel Hidalgo, Ciudad de México, 11500"
    )

    def _try_select_store_ui(self, page, location: Location):
        # Cookie banner can cover the locator button.
        try:
            cookie = page.locator(
                ".chedrauimx-frontend-applications-5-x-cookiesButtonAccept"
            ).first
            if cookie.count() and cookie.is_visible():
                cookie.click(timeout=4_000)
                page.wait_for_timeout(300)
        except Exception:
            self._click_text(page, ("Aceptar",), timeout=2_000)

        opened = False
        try:
            button = page.locator(
                "button.chedrauimx-locator-2-x-labelTextAddress"
            ).first
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
                ),
            )
        self.run_meta["location_button_opened"] = opened
        if not opened:
            self._save_diagnostics(page, "store_location_button_not_found")
            return False, None
        page.wait_for_timeout(600)

        # The live modal uses tabs "Enviar a" / "Recoger en".
        pickup_clicked = self._click_text(
            page,
            ("Recoger en una tienda", "Recoger en tienda", "Recoger en"),
        )
        self.run_meta["pickup_clicked"] = pickup_clicked
        page.wait_for_timeout(500)

        # The live UI asks for a full address, not a postal code.
        address_filled = False
        address_input = None
        try:
            candidates = page.locator("input")
            for i in range(min(candidates.count(), 60)):
                inp = candidates.nth(i)
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
                    )
                    normalized = self._normalize(hint)
                    if (
                        "miguel de cervantes" in normalized
                        or "direccion" in normalized
                        or "ubicacion" in normalized
                    ):
                        address_input = inp
                        inp.fill(self.POLANCO_ADDRESS)
                        address_filled = True
                        break
                except Exception:
                    continue
        except Exception:
            pass
        self.run_meta["address_filled"] = address_filled

        if address_filled and address_input is not None:
            page.wait_for_timeout(1_400)
            # Google/VTEX autocomplete normally exposes role=option/listbox. Prefer
            # a real suggestion; keyboard fallback follows normal UI behaviour.
            suggestion_clicked = False
            try:
                options = page.locator('[role="option"]')
                for i in range(min(options.count(), 20)):
                    option = options.nth(i)
                    if option.is_visible():
                        text = self._normalize(option.inner_text(timeout=1_500))
                        if any(
                            token in text
                            for token in (
                                "miguel de cervantes",
                                "irrigacion",
                                "polanco",
                                "11500",
                            )
                        ):
                            option.click(timeout=4_000)
                            suggestion_clicked = True
                            break
            except Exception:
                pass
            if not suggestion_clicked:
                try:
                    address_input.press("ArrowDown")
                    address_input.press("Enter")
                    suggestion_clicked = True
                except Exception:
                    pass
            self.run_meta["address_suggestion_selected"] = suggestion_clicked
            page.wait_for_timeout(1_800)

        # Once the address is confirmed Chedraui lists nearby Pickup stores.
        store_clicked = False
        store_patterns = (
            "Chedraui Selecto México Polanco",
            "Selecto México Polanco",
            "Selecto Mexico Polanco",
            "México Polanco",
            "Mexico Polanco",
        )
        for label in store_patterns:
            try:
                nodes = page.get_by_text(label, exact=False)
                for i in range(min(nodes.count(), 20)):
                    node = nodes.nth(i)
                    if node.is_visible():
                        node.click(timeout=4_000)
                        store_clicked = True
                        break
                if store_clicked:
                    break
            except Exception:
                continue

        # Fallback: directory is part of Chedraui's normal UI. It is useful if
        # the address autocomplete does not immediately populate nearby stores.
        if not store_clicked:
            directory_clicked = self._click_text(page, ("Directorio de tiendas",))
            self.run_meta["directory_clicked"] = directory_clicked
            if directory_clicked:
                page.wait_for_timeout(1_200)
                for label in store_patterns:
                    try:
                        nodes = page.get_by_text(label, exact=False)
                        for i in range(min(nodes.count(), 30)):
                            node = nodes.nth(i)
                            if node.is_visible():
                                node.click(timeout=4_000)
                                store_clicked = True
                                break
                        if store_clicked:
                            break
                    except Exception:
                        continue

        self.run_meta["store_clicked"] = store_clicked
        page.wait_for_timeout(500)

        # The live call-to-action is exactly "Recoger en esta tienda".
        confirmed = False
        try:
            button = page.get_by_text("Recoger en esta tienda", exact=False).first
            if button.count() and button.is_visible():
                parent_button = button.locator("xpath=ancestor::button[1]")
                if parent_button.count() and parent_button.is_enabled():
                    parent_button.click(timeout=5_000)
                    confirmed = True
                elif button.is_enabled():
                    button.click(timeout=5_000)
                    confirmed = True
        except Exception:
            pass
        if not confirmed and store_clicked:
            confirmed = self._click_text(
                page,
                ("Seleccionar", "Elegir", "Confirmar", "Guardar", "Continuar"),
                timeout=4_000,
            )
        self.run_meta["pickup_confirmed"] = confirmed
        page.wait_for_timeout(1_200)

        verified, method = self._verify_store_context(page, location)
        if verified:
            return True, f"{method}_after_address_ui"

        self.run_meta["store_modal_text"] = self._body_text(page)[:10_000]
        self._save_diagnostics(page, "store_address_selection_unverified")
        return False, None


__all__ = [
    "ChedrauiBlocked",
    "ChedrauiStoreContextError",
    "ChedrauiScraper",
]
