from __future__ import annotations

from .chedraui import (
    ChedrauiBlocked,
    ChedrauiScraper as BaseChedrauiScraper,
    ChedrauiStoreContextError,
)
from ..config import Location


class ChedrauiScraper(BaseChedrauiScraper):
    """Chedraui scraper using the live VTEX store directory for Polanco."""

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
        selector = "button.chedrauimx-locator-2-x-labelTextAddress"
        try:
            page.locator(selector).first.wait_for(state="visible", timeout=7_000)
            page.locator(selector).first.click(timeout=5_000)
            page.wait_for_timeout(650)
            return True
        except Exception:
            pass
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
        if opened:
            page.wait_for_timeout(650)
        return opened

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

    def _try_select_store_ui(self, page, location: Location):
        self.run_meta["cookie_clicked"] = self._accept_cookies(page)

        opened = self._open_locator(page)
        self.run_meta["location_button_opened"] = opened
        if not opened:
            self._save_diagnostics(page, "store_location_button_not_found")
            return False, None

        pickup_clicked = self._click_text(
            page,
            ("Recoger en una tienda", "Recoger en tienda", "Recoger en"),
        )
        self.run_meta["pickup_clicked"] = pickup_clicked
        page.wait_for_timeout(500)

        directory_clicked = self._click_text(page, ("Directorio de tiendas",))
        self.run_meta["directory_clicked"] = directory_clicked
        if not directory_clicked:
            self._save_diagnostics(page, "store_directory_not_found")
            return False, None
        page.wait_for_timeout(800)

        state_selected = False
        city_selected = False
        city_options: list[str] = []
        try:
            selects = page.locator("select.chedrauimx-locator-2-x-selectOption")
            if selects.count() >= 1:
                selects.nth(0).select_option(label="CDMX y area Metropolitana")
                state_selected = True
                page.wait_for_timeout(1_200)

            if selects.count() >= 2:
                city = selects.nth(1)
                try:
                    city_options = [
                        x.strip()
                        for x in city.locator("option").all_text_contents()
                        if x.strip()
                    ]
                except Exception:
                    city_options = []
                preferred = next(
                    (
                        value
                        for value in city_options
                        if any(
                            token in self._normalize(value)
                            for token in (
                                "miguel hidalgo",
                                "ciudad de mexico",
                                "cdmx",
                                "polanco",
                            )
                        )
                    ),
                    None,
                )
                if preferred:
                    city.select_option(label=preferred)
                    city_selected = True
                    page.wait_for_timeout(1_200)
        except Exception as exc:
            self.run_meta["directory_select_error"] = str(exc)

        self.run_meta["state_selected"] = state_selected
        self.run_meta["city_selected"] = city_selected
        self.run_meta["city_options"] = city_options[:80]

        # Selecting the state alone may already populate every CDMX store.
        store_clicked = self._click_polanco(page)
        self.run_meta["store_clicked"] = store_clicked
        page.wait_for_timeout(500)

        confirmed = self._click_enabled_pickup_button(page)
        if not confirmed and store_clicked:
            confirmed = self._click_text(
                page,
                ("Seleccionar", "Elegir", "Confirmar", "Guardar", "Continuar"),
                timeout=4_000,
            )
        self.run_meta["pickup_confirmed"] = confirmed
        page.wait_for_timeout(1_400)

        verified, method = self._verify_store_context(page, location)
        if verified:
            return True, f"{method}_after_directory_ui"

        self.run_meta["store_modal_text"] = self._body_text(page)[:14_000]
        self._save_diagnostics(page, "store_directory_selection_unverified")
        return False, None


__all__ = [
    "ChedrauiBlocked",
    "ChedrauiStoreContextError",
    "ChedrauiScraper",
]
