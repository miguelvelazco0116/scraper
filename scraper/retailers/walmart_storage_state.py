from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from ..config import Category, Location
from .walmart import WalmartScraper, WalmartStoreContextError


class WalmartStorageStateScraper(WalmartScraper):
    """Walmart scraper that reuses a portable Playwright storage state.

    The state file is expected to contain either a standard Playwright
    storage_state object or a wrapper with keys `storage_state` and
    `session_storage`. It must be treated as a secret and never committed.
    """

    def __init__(self, storage_state_path: str | Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self.storage_state_path = Path(storage_state_path)
        if not self.storage_state_path.exists():
            raise FileNotFoundError(f"Storage state Walmart no encontrado: {self.storage_state_path}")

    def _load_state(self) -> tuple[dict, dict]:
        payload = json.loads(self.storage_state_path.read_text(encoding="utf-8"))
        if "storage_state" in payload:
            storage_state = payload.get("storage_state") or {"cookies": [], "origins": []}
            session_storage = payload.get("session_storage") or {}
        else:
            storage_state = payload
            session_storage = {}
        return storage_state, session_storage

    def scrape_category(self, category: Category, location: Location) -> list[dict]:
        if self.require_store_context and (not location.store_id or not location.store):
            raise WalmartStoreContextError("Walmart requiere una tienda configurada para esta corrida.")

        storage_state, session_storage = self._load_state()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context(
                locale="es-MX",
                viewport={"width": 1440, "height": 1000},
                storage_state=storage_state,
            )
            try:
                if session_storage:
                    serialized = json.dumps(session_storage, ensure_ascii=False)
                    context.add_init_script(
                        f"""
                        (() => {{
                          const saved = {serialized};
                          const host = window.location.hostname;
                          const values = saved[host] || saved['www.walmart.com.mx'] || {{}};
                          for (const [key, value] of Object.entries(values)) {{
                            try {{ window.sessionStorage.setItem(key, value); }} catch (e) {{}}
                          }}
                        }})();
                        """
                    )

                return self._scrape_with_context(
                    context,
                    category,
                    location,
                    establish_reference=False,
                )
            finally:
                context.close()
                browser.close()
