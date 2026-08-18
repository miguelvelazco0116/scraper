from __future__ import annotations

from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

from ..config import Category, Location
from .walmart import WalmartScraper, WalmartStoreContextError


class WalmartPersistentScraper(WalmartScraper):
    """Walmart scraper that reuses a local Playwright browser profile.

    The profile must be created and verified by the user in a normal visible
    browser session. It must never be committed to GitHub.
    """

    def __init__(self, user_data_dir: str | Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self.user_data_dir = Path(user_data_dir)
        self.user_data_dir.mkdir(parents=True, exist_ok=True)

    def scrape_category(self, category: Category, location: Location) -> list[dict]:
        if self.require_store_context and (not location.store_id or not location.store):
            raise WalmartStoreContextError("Walmart requiere una tienda configurada para esta corrida.")

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir),
                headless=self.headless,
                locale="es-MX",
                viewport={"width": 1440, "height": 1000},
            )
            page = context.pages[0] if context.pages else context.new_page()
            try:
                rows: list[dict] = []
                seen_skus: set[str] = set()
                no_new_pages = 0
                verified_any_page = False

                for page_number in range(1, self.max_pages + 1):
                    url = self._paged_url(category.url, page_number)
                    response = page.goto(url, wait_until="domcontentloaded", timeout=120_000)
                    self._assert_not_blocked(page, response.status if response else None)
                    page.wait_for_timeout(self.wait_ms)

                    body = self._body_text(page)
                    verified = self._store_context_in_text(body, location)
                    if not verified:
                        verified = self._try_select_store_ui(page, location)
                    verified_any_page = verified_any_page or verified
                    self.run_meta.setdefault("pages", []).append(
                        {"page": page_number, "url": page.url, "store_context_verified": verified}
                    )

                    if self.require_store_context and not verified:
                        self._save_diagnostics(page, f"store_context_missing_page_{page_number}")
                        raise WalmartStoreContextError(
                            f"El perfil no tiene contexto válido para {location.store} / {location.postal_code}. "
                            "Ejecuta scripts/walmart_prepare_session.py y vuelve a intentar."
                        )

                    try:
                        page.wait_for_selector('a[href*="/ip/"]', timeout=20_000)
                    except PlaywrightTimeoutError:
                        self._save_diagnostics(page, f"no_products_page_{page_number}")
                        break

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

                self.run_meta["store_context_verified"] = verified_any_page
                self.run_meta["unique_products"] = len(seen_skus)
                self._save_diagnostics(page, f"success_{category.id}_{location.id}")
                return rows
            finally:
                context.close()
