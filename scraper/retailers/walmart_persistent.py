from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

from ..config import Category, Location
from .walmart import WalmartScraper, WalmartStoreContextError


class WalmartPersistentScraper(WalmartScraper):
    """Walmart scraper that reuses a local Playwright browser profile.

    A store context is still verified on the first category page. The profile is
    only a way to retain a normal user session; it is not treated as proof by
    itself and must never be committed to GitHub.
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
            try:
                return self._scrape_with_context(
                    context,
                    category,
                    location,
                    establish_reference=False,
                )
            finally:
                context.close()
