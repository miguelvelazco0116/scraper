from __future__ import annotations

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
        cookie_clicked = False
        try:
            cookie = page.locator(
                ".chedrauimx-frontend-applications-5-x-cookiesButtonAccept"
            ).first
            if cookie.count() and cookie.is_visible():
                cookie.click(timeout=4_000)
                cookie_clicked = True
        except Exception:
            pass
        if not cookie_clicked:
            cookie_clicked = self._click_text(page, ("Aceptar",), timeout=4_000)
        self.run_meta["cookie_clicked"] = cookie_clicked
        page.wait_for_timeout(900)

        opened = False
        locator_selector = "button.chedrauimx-locator-2-x-labelTextAddress"
        try:
            page.locator(locator_selector).first.wait_for(state="visible", timeout=7_000)
            button = page.locator(locator_selector).first
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
                    "Agregar una",
                ),
                timeout=5_000,
            )
        self.run_meta["location_button_opened"] = opened
        if not opened:
            self._save_diagnostics(page, "store_location_button_not_found")
            return False, None
        page.wait_for_timeout(700)

        pickup_clicked = self._click_text(
            page,
            ("Recoger en una tienda", "Recoger en tienda", "Recoger en"),
        )
        self.run_meta["pickup_clicked"] = pickup_clicked
        page.wait_for_timeout(500)

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
            page.wait_for_timeout(1_500)
            suggestion_clicked = False
            try:
                options = page.locator(
                    ".chedrauimx-locator-2-x-InputSelect__content_select_list_item"
                )
                for i in range(min(options.count(), 20)):
                    option = options.nth(i)
                    if not option.is_visible():
                        continue
                    text = self._normalize(option.inner_text(timeout=1_500))
                    if any(
                        token in text
                        for token in (
                            "miguel de cervantes",
                            "irrigacion",
                            "11500",
                        )
                    ):
                        option.click(timeout=5_000)
                        suggestion_clicked = True
                        break
            except Exception:
                pass
            if not suggestion_clicked:
                try:
                    address_input.press("ArrowDown")
                    page.wait_for_timeout(200)
                    address_input.press("Enter")
                    suggestion_clicked = True
                except Exception:
                    pass
            self.run_meta["address_suggestion_selected"] = suggestion_clicked
            page.wait_for_timeout(2_200)

        store_clicked = False
        store_patterns = (
            "Chedraui Selecto México Polanco",
            "CHEDRAUI SELECTO MEXICO POLANCO",
            "Selecto México Polanco",
            "Selecto Mexico Polanco",
            "México Polanco",
            "Mexico Polanco",
        )
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

        if not store_clicked:
            directory_clicked = self._click_text(page, ("Directorio de tiendas",))
            self.run_meta["directory_clicked"] = directory_clicked
            if directory_clicked:
                page.wait_for_timeout(1_500)
                for label in store_patterns:
                    try:
                        nodes = page.get_by_text(label, exact=False)
                        for i in range(min(nodes.count(), 40)):
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

        confirmed = False
        try:
            labels = page.get_by_text("Recoger en esta tienda", exact=False)
            for i in range(min(labels.count(), 10)):
                label = labels.nth(i)
                if not label.is_visible():
                    continue
                parent_button = label.locator("xpath=ancestor::button[1]")
                if parent_button.count() and parent_button.is_enabled():
                    parent_button.click(timeout=5_000)
                    confirmed = True
                    break
        except Exception:
            pass
        if not confirmed and store_clicked:
            confirmed = self._click_text(
                page,
                ("Seleccionar", "Elegir", "Confirmar", "Guardar", "Continuar"),
                timeout=4_000,
            )
        self.run_meta["pickup_confirmed"] = confirmed
        page.wait_for_timeout(1_300)

        verified, method = self._verify_store_context(page, location)
        if verified:
            return True, f"{method}_after_address_ui"

        self.run_meta["store_modal_text"] = self._body_text(page)[:12_000]
        self._save_diagnostics(page, "store_address_selection_unverified")
        return False, None


__all__ = [
    "ChedrauiBlocked",
    "ChedrauiStoreContextError",
    "ChedrauiScraper",
]
