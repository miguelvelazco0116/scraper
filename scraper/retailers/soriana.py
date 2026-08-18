from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext, Page, Response, TimeoutError as PlaywrightTimeoutError, sync_playwright

from ..config import Category, Location
from ..parsers import absolute_url, clean_text, extract_sku, parse_money

PRODUCT_SELECTOR = ".product, .product-tile, [data-pid]"
BLOCK_MARKERS = ("GF R01", "Access Denied", "Forbidden")


class SorianaBlocked(RuntimeError):
    pass


class SorianaScraper:
    """Browser-based scraper that follows normal storefront navigation.

    It intentionally does not implement CAPTCHA solving, proxy rotation,
    fingerprint spoofing, or other anti-bot bypass techniques. If Soriana
    blocks the session, diagnostics are saved and the run stops.
    """

    def __init__(
        self,
        headless: bool = True,
        diagnostics_dir: str | Path = "diagnostics",
        max_load_more: int = 100,
        wait_ms: int = 1200,
    ) -> None:
        self.headless = headless
        self.diagnostics_dir = Path(diagnostics_dir)
        self.diagnostics_dir.mkdir(parents=True, exist_ok=True)
        self.max_load_more = max_load_more
        self.wait_ms = wait_ms
        self.grid_responses: list[dict[str, Any]] = []

    def _capture_grid_response(self, response: Response) -> None:
        if "Search-UpdateGrid" not in response.url:
            return
        record: dict[str, Any] = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "status": response.status,
            "url": response.url,
        }
        try:
            text = response.text()
            record["body_length"] = len(text)
        except Exception as exc:
            record["read_error"] = str(exc)
        self.grid_responses.append(record)

    def _save_diagnostics(self, page: Page, prefix: str) -> None:
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", prefix)
        try:
            page.screenshot(path=str(self.diagnostics_dir / f"{safe}.png"), full_page=True)
        except Exception:
            pass
        try:
            (self.diagnostics_dir / f"{safe}.html").write_text(page.content(), encoding="utf-8")
        except Exception:
            pass
        (self.diagnostics_dir / "grid_responses.json").write_text(
            json.dumps(self.grid_responses, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _assert_not_blocked(self, page: Page, status: int | None = None) -> None:
        body = ""
        try:
            body = page.locator("body").inner_text(timeout=5_000)
        except Exception:
            pass
        blocked = status == 403 or any(marker.lower() in body.lower() for marker in BLOCK_MARKERS)
        if blocked:
            self._save_diagnostics(page, "blocked")
            raise SorianaBlocked(
                "Soriana bloqueó la sesión (403/GF R01). Se guardaron diagnósticos; "
                "el scraper no intenta evadir la protección del sitio."
            )

    def _extract_cards(self, page: Page, category: Category, location: Location) -> list[dict[str, Any]]:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        js = r"""
        els => els.map(el => {
          const text = (sel) => {
            const node = el.querySelector(sel);
            return node ? node.textContent.trim() : null;
          };
          const attr = (sel, name) => {
            const node = el.querySelector(sel);
            return node ? node.getAttribute(name) : null;
          };
          const link = el.querySelector('.pdp-link a, a[href*=".html"], a.link');
          const priceNodes = Array.from(el.querySelectorAll('.price .value, .sales .value, .price, [class*="price"]'))
            .map(n => n.textContent.trim())
            .filter(Boolean);
          const promoNodes = Array.from(el.querySelectorAll('.promotions, .promotion, [class*="promo"], .callout, .badge'))
            .map(n => n.textContent.trim())
            .filter(Boolean);
          return {
            data_pid: el.getAttribute('data-pid') || attr('[data-pid]', 'data-pid'),
            product: text('.pdp-link a') || text('.product-name') || (link ? link.textContent.trim() : null),
            url: link ? link.getAttribute('href') : null,
            price_texts: [...new Set(priceNodes)],
            promo_texts: [...new Set(promoNodes)]
          };
        })
        """
        if not page.locator(PRODUCT_SELECTOR).count():
            return []
        raw = page.locator(PRODUCT_SELECTOR).evaluate_all(js)
        out: list[dict[str, Any]] = []
        for item in raw:
            url = absolute_url(item.get("url"))
            product = clean_text(item.get("product"))
            if not product or not url:
                continue

            money_texts = item.get("price_texts") or []
            values: list[float] = []
            for txt in money_texts:
                for token in re.findall(r"\$\s*[\d,]+(?:\.\d{1,2})?", txt):
                    val = parse_money(token)
                    if val is not None and val not in values:
                        values.append(val)

            current_price = values[0] if values else None
            regular_price = values[1] if len(values) > 1 else current_price
            if current_price and regular_price and current_price > regular_price:
                current_price, regular_price = regular_price, current_price

            out.append(
                {
                    "scrape_timestamp": now,
                    "retailer": "Soriana",
                    "city": location.city,
                    "state": location.state,
                    "postal_code": location.postal_code,
                    "store": location.store,
                    "category": category.name,
                    "category_id": category.id,
                    "sku": extract_sku(url, item.get("data_pid")),
                    "brand": self._infer_brand(product),
                    "product": product,
                    "price_current": current_price,
                    "price_regular": regular_price,
                    "promotion": clean_text(" | ".join(item.get("promo_texts") or [])),
                    "url": url,
                    "price_raw": clean_text(" | ".join(money_texts)),
                }
            )
        return out

    @staticmethod
    def _infer_brand(product: str | None) -> str | None:
        if not product:
            return None
        known = [
            "Colgate", "Oral-B", "Listerine", "Sensodyne", "Crest", "Gum",
            "Curaprox", "Philips", "Pro", "Aquafresh", "Bexident", "Corega",
        ]
        lower = product.lower()
        for brand in known:
            if brand.lower() in lower:
                return brand
        return None

    def _click_more_until_done(self, page: Page) -> None:
        selectors = [
            ".show-more button",
            ".show-more a",
            "button:has-text('Ver más')",
            "a:has-text('Ver más')",
            "button:has-text('Mostrar más')",
            "a:has-text('Mostrar más')",
        ]
        for _ in range(self.max_load_more):
            before = page.locator(PRODUCT_SELECTOR).count()
            clicked = False
            for selector in selectors:
                loc = page.locator(selector).first
                try:
                    if loc.count() and loc.is_visible(timeout=1_000) and loc.is_enabled(timeout=1_000):
                        loc.scroll_into_view_if_needed()
                        loc.click(timeout=10_000)
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                break
            try:
                page.wait_for_function(
                    "([sel, n]) => document.querySelectorAll(sel).length > n",
                    arg=[PRODUCT_SELECTOR, before],
                    timeout=15_000,
                )
            except PlaywrightTimeoutError:
                page.wait_for_timeout(self.wait_ms)
            page.wait_for_timeout(self.wait_ms)
            self._assert_not_blocked(page)

    def scrape_category(self, category: Category, location: Location) -> list[dict[str, Any]]:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context: BrowserContext = browser.new_context(
                locale="es-MX",
                viewport={"width": 1440, "height": 1000},
            )
            page = context.new_page()
            page.on("response", self._capture_grid_response)
            try:
                response = page.goto(category.url, wait_until="domcontentloaded", timeout=120_000)
                status = response.status if response else None
                self._assert_not_blocked(page, status)
                page.wait_for_timeout(2_000)
                try:
                    page.wait_for_selector(PRODUCT_SELECTOR, timeout=25_000)
                except PlaywrightTimeoutError:
                    self._save_diagnostics(page, "no_products")
                    return []

                self._click_more_until_done(page)
                rows = self._extract_cards(page, category, location)
                self._save_diagnostics(page, f"success_{category.id}")
                return rows
            finally:
                context.close()
                browser.close()
