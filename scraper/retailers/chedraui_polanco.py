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

    def _store_specific_total(self, page) -> int | None:
        """Read the displayed category count after Polanco has been selected."""
        text = self._body_text(page)
        match = re.search(r"\b([\d,]+)\s+Productos\b", text, flags=re.IGNORECASE)
        if not match:
            return None
        try:
            return int(match.group(1).replace(",", ""))
        except ValueError:
            return None

    def _scroll_grid(self, page) -> None:
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(700)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(350)
        except Exception:
            pass

    def _rows_from_current_page(self, page, category, location) -> list[dict]:
        try:
            page.wait_for_selector(PRODUCT_SELECTOR, timeout=10_000)
        except Exception:
            return []
        self._scroll_grid(page)
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
            response = page.goto(previous_url, wait_until="domcontentloaded", timeout=60_000)
            self._assert_not_blocked(page, response.status if response else None)
            page.wait_for_timeout(max(self.wait_ms, 900))
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

            page.wait_for_timeout(1_400)
            rows = self._rows_from_current_page(page, category, location)
            info["rows"] = len(rows)
            return rows, info
        except Exception as exc:
            info["reason"] = f"fallback_error:{type(exc).__name__}"
            return [], info

    def _load_page_rows(self, page, category, location, page_number: int, target_rows: int | None):
        """Retry a VTEX result page and retain the most complete render."""
        url = self._paged_url(category.url, page_number)
        best: dict[str, dict] = {}
        attempts: list[dict] = []

        for attempt in range(1, 5):
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                self._assert_not_blocked(page, response.status if response else None)
                page.wait_for_timeout(max(self.wait_ms, 1_000))
                rows = self._rows_from_current_page(page, category, location)
            except Exception as exc:
                attempts.append(
                    {"attempt": attempt, "rows": 0, "reason": f"navigation:{type(exc).__name__}"}
                )
                continue

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

        if not best and page_number > 1:
            recovered, fallback_info = self._recover_page_from_previous(
                page, category, location, page_number
            )
            for row in recovered:
                key = str(row.get("sku") or row.get("url"))
                if key:
                    best[key] = row
            fallback_info["best"] = len(best)
            attempts.append(fallback_info)

        if not best:
            self._save_diagnostics(page, f"pagination_empty_page_{page_number}")

        return url, list(best.values()), attempts

    def _collect_pages(self, page, category, location):
        rows_by_key: dict[str, dict] = {}
        max_pages = self.max_pages if self.max_pages > 0 else 100

        displayed_total = self._store_specific_total(page)
        self.run_meta["displayed_category_products"] = displayed_total
        page_size: int | None = None
        expected_pages: int | None = None
        consecutive_stale = 0
        pending_empty_pages: list[int] = []
        internal_gaps: list[int] = []
        partial_page: int | None = None

        for page_number in range(1, max_pages + 1):
            if displayed_total is not None and len(rows_by_key) >= displayed_total:
                break

            if page_size and displayed_total:
                expected_pages = math.ceil(displayed_total / page_size)
                self.run_meta["displayed_expected_pages"] = expected_pages
                if page_number < expected_pages:
                    target_rows = page_size
                else:
                    target_rows = None
            else:
                target_rows = None

            url, page_rows, attempts = self._load_page_rows(
                page, category, location, page_number, target_rows
            )

            if displayed_total is None and page_rows:
                displayed_total = self._store_specific_total(page)
                self.run_meta["displayed_category_products"] = displayed_total

            if page_size is None and page_rows:
                page_size = len(page_rows)
                self.run_meta["page_size"] = page_size

            before = len(rows_by_key)
            for row in page_rows:
                key = str(row.get("sku") or row.get("url"))
                if key:
                    rows_by_key[key] = row
            new_count = len(rows_by_key) - before

            if page_rows:
                if pending_empty_pages:
                    internal_gaps.extend(pending_empty_pages)
                    pending_empty_pages = []
                if page_size and len(page_rows) < page_size:
                    partial_page = page_number
            else:
                pending_empty_pages.append(page_number)

            if new_count == 0:
                consecutive_stale += 1
            else:
                consecutive_stale = 0

            self.run_meta.setdefault("pages", []).append(
                {
                    "page": page_number,
                    "url": page.url or url,
                    "rows": len(page_rows),
                    "new": new_count,
                    "attempts": attempts,
                }
            )

            # A partial last page followed by an empty page proves the listing
            # is exhausted even if the displayed category count includes items
            # that are not listable for the selected store.
            if partial_page is not None and page_number > partial_page and new_count == 0:
                break

            # If there is no partial page, two consecutive pages without new
            # products are the end-of-list guard.
            if consecutive_stale >= 2:
                break

        self.run_meta["collected_products"] = len(rows_by_key)
        self.run_meta["partial_last_page"] = partial_page
        self.run_meta["internal_pagination_gaps"] = internal_gaps
        self.run_meta["listing_exhausted"] = not internal_gaps
        if displayed_total is not None:
            self.run_meta["displayed_count_coverage"] = round(
                len(rows_by_key) / displayed_total, 4
            ) if displayed_total else None

        if internal_gaps:
            self._save_diagnostics(page, "pagination_internal_gap")
            raise RuntimeError(
                f"Chedraui dejó huecos internos de paginación sin recuperar: {internal_gaps}"
            )

        return list(rows_by_key.values())


__all__ = [
    "ChedrauiBlocked",
    "ChedrauiStoreContextError",
    "ChedrauiScraper",
]
