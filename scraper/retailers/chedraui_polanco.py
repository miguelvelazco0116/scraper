from __future__ import annotations

import math
import re

from .chedraui import (
    PRODUCT_SELECTOR,
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

    def _store_specific_total(self, page) -> int | None:
        """Read the product count after Polanco has been selected."""
        text = self._body_text(page)
        match = re.search(r"\b([\d,]+)\s+Productos\b", text, flags=re.IGNORECASE)
        if not match:
            return None
        try:
            return int(match.group(1).replace(",", ""))
        except ValueError:
            return None

    def _load_page_rows(self, page, category, location, page_number: int, target_rows: int | None):
        """Retry a VTEX result page and retain the most complete render."""
        url = self._paged_url(category.url, page_number)
        best: dict[str, dict] = {}
        attempts: list[dict] = []

        for attempt in range(1, 5):
            response = page.goto(url, wait_until="domcontentloaded", timeout=120_000)
            self._assert_not_blocked(page, response.status if response else None)
            page.wait_for_timeout(max(self.wait_ms, 1_100))

            # Product cards are lazy-rendered; force the complete grid into view.
            try:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(700)
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(350)
            except Exception:
                pass

            try:
                page.wait_for_selector(PRODUCT_SELECTOR, timeout=12_000)
            except Exception:
                attempts.append({"attempt": attempt, "rows": 0, "reason": "selector_timeout"})
                continue

            rows = self._extract_cards(page, category, location)
            for row in rows:
                key = str(row.get("sku") or row.get("url"))
                if key:
                    best[key] = row
            attempts.append({"attempt": attempt, "rows": len(rows), "best": len(best)})

            if target_rows is None:
                if best:
                    break
            elif len(best) >= target_rows:
                break

        return url, list(best.values()), attempts

    def _collect_pages(self, page, category, location):
        rows_by_key: dict[str, dict] = {}
        max_pages = self.max_pages if self.max_pages > 0 else 100

        expected_total = self._store_specific_total(page)
        self.run_meta["expected_store_products"] = expected_total
        page_size: int | None = None
        expected_pages: int | None = None
        consecutive_empty = 0

        for page_number in range(1, max_pages + 1):
            if expected_total is not None and len(rows_by_key) >= expected_total:
                break
            if expected_pages is not None and page_number > expected_pages + 1:
                break

            if page_size and expected_total:
                if expected_pages is None:
                    expected_pages = math.ceil(expected_total / page_size)
                if page_number < expected_pages:
                    target_rows = page_size
                elif page_number == expected_pages:
                    target_rows = max(1, expected_total - page_size * (expected_pages - 1))
                else:
                    target_rows = None
            else:
                target_rows = None

            url, page_rows, attempts = self._load_page_rows(
                page, category, location, page_number, target_rows
            )

            if page_size is None and page_rows:
                page_size = len(page_rows)
                if expected_total:
                    expected_pages = math.ceil(expected_total / page_size)
                    self.run_meta["page_size"] = page_size
                    self.run_meta["expected_pages"] = expected_pages

            before = len(rows_by_key)
            for row in page_rows:
                key = str(row.get("sku") or row.get("url"))
                if key:
                    rows_by_key[key] = row
            new_count = len(rows_by_key) - before

            self.run_meta.setdefault("pages", []).append(
                {
                    "page": page_number,
                    "url": page.url or url,
                    "rows": len(page_rows),
                    "new": new_count,
                    "attempts": attempts,
                }
            )

            if not page_rows:
                consecutive_empty += 1
            else:
                consecutive_empty = 0

            # With a known store-specific count, do not stop on one transient
            # empty VTEX page. Without a count, two empty pages remain the guard.
            if expected_total is None and consecutive_empty >= 2:
                break
            if expected_total is not None and expected_pages is not None:
                if page_number >= expected_pages and len(rows_by_key) >= expected_total:
                    break
                if consecutive_empty >= 2 and page_number >= expected_pages:
                    break

        self.run_meta["collected_products"] = len(rows_by_key)
        if expected_total is not None:
            self.run_meta["coverage_ratio"] = round(
                len(rows_by_key) / expected_total, 4
            ) if expected_total else None
        return list(rows_by_key.values())


__all__ = [
    "ChedrauiBlocked",
    "ChedrauiStoreContextError",
    "ChedrauiScraper",
]
