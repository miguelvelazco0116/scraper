from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from ..config import Category, Location
from ..parsers import absolute_url, clean_text, extract_sku, parse_money

BASE_URL = "https://www.walmart.com.mx/"
BLOCK_MARKERS = (
    "access denied",
    "robot or human",
    "captcha",
    "reference #18",
    "forbidden",
)


class WalmartBlocked(RuntimeError):
    pass


class WalmartStoreContextError(RuntimeError):
    pass


class WalmartScraper:
    """Playwright scraper for public Walmart Mexico category pages.

    The scraper follows Walmart's normal storefront and pagination URLs. It does
    not solve CAPTCHAs, rotate proxies, spoof fingerprints, or bypass access
    controls. When store context cannot be verified, the run stops rather than
    labeling national/marketplace prices as store-specific.
    """

    def __init__(
        self,
        headless: bool = True,
        diagnostics_dir: str | Path = "diagnostics",
        max_pages: int = 100,
        wait_ms: int = 1200,
        require_store_context: bool = True,
        store_only: bool = True,
    ) -> None:
        self.headless = headless
        self.diagnostics_dir = Path(diagnostics_dir)
        self.diagnostics_dir.mkdir(parents=True, exist_ok=True)
        self.max_pages = max_pages
        self.wait_ms = wait_ms
        self.require_store_context = require_store_context
        self.store_only = store_only
        self.run_meta: dict[str, Any] = {}

    @staticmethod
    def _paged_url(url: str, page_number: int) -> str:
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        if page_number <= 1:
            query.pop("page", None)
        else:
            query["page"] = str(page_number)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    def _save_diagnostics(self, page: Page, prefix: str) -> None:
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", prefix)
        try:
            page.screenshot(path=str(self.diagnostics_dir / f"walmart_{safe}.png"), full_page=True)
        except Exception:
            pass
        try:
            (self.diagnostics_dir / f"walmart_{safe}.html").write_text(page.content(), encoding="utf-8")
        except Exception:
            pass
        (self.diagnostics_dir / "walmart_run_meta.json").write_text(
            json.dumps(self.run_meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _body_text(self, page: Page) -> str:
        try:
            return page.locator("body").inner_text(timeout=8_000)
        except Exception:
            return ""

    def _assert_not_blocked(self, page: Page, status: int | None = None) -> None:
        body = self._body_text(page).lower()
        if status in (401, 403, 429) or any(marker in body for marker in BLOCK_MARKERS):
            self._save_diagnostics(page, "blocked")
            raise WalmartBlocked(
                "Walmart bloqueó o desafió la sesión automatizada. Se guardaron diagnósticos; "
                "el scraper no intenta evadir la protección del sitio."
            )

    @staticmethod
    def _store_context_in_text(text: str, location: Location) -> bool:
        lower = text.lower()
        store_ok = bool(location.store and location.store.lower() in lower)
        cp_ok = bool(location.postal_code and location.postal_code in text)
        store_id_ok = bool(location.store_id and f"#{location.store_id}" in text)
        return store_ok and (cp_ok or store_id_ok)

    def _try_select_store_ui(self, page: Page, location: Location) -> bool:
        """Best-effort use of Walmart's own location/store UI."""
        try:
            trigger = page.get_by_text("¿Cómo quieres tus artículos?", exact=False).first
            if trigger.count():
                trigger.click(timeout=5_000)
                page.wait_for_timeout(700)
        except Exception:
            pass

        for label in ("Pickup", "Recoger", "Recoge"):
            try:
                candidate = page.get_by_text(label, exact=False).first
                if candidate.count() and candidate.is_visible():
                    candidate.click(timeout=3_000)
                    page.wait_for_timeout(500)
                    break
            except Exception:
                pass

        # Search visible inputs for postal-code or store search fields.
        inputs = page.locator("input")
        try:
            count = min(inputs.count(), 30)
        except Exception:
            count = 0
        for i in range(count):
            inp = inputs.nth(i)
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
                ).lower()
                if any(word in hint for word in ("código", "codigo", "postal", "ubic", "tienda", "store")):
                    inp.fill(location.postal_code or location.store or "")
                    inp.press("Enter")
                    page.wait_for_timeout(1_200)
                    break
            except Exception:
                continue

        if location.store:
            try:
                store_choice = page.get_by_text(location.store, exact=False).first
                if store_choice.count() and store_choice.is_visible():
                    store_choice.click(timeout=4_000)
                    page.wait_for_timeout(1_200)
            except Exception:
                pass

        return self._store_context_in_text(self._body_text(page), location)

    def _establish_store_context(self, page: Page, location: Location) -> bool:
        if not location.store_id or not location.store:
            return not self.require_store_context

        store_url = f"https://www.walmart.com.mx/tienda/{location.store_id}"
        response = page.goto(store_url, wait_until="domcontentloaded", timeout=120_000)
        self._assert_not_blocked(page, response.status if response else None)
        page.wait_for_timeout(1_000)

        # Walmart exposes the store page publicly; use any normal favorite-store
        # action when available to persist context in cookies/local storage.
        try:
            favorite = page.get_by_text("Guardar como mi tienda favorita", exact=False).first
            if favorite.count() and favorite.is_visible():
                favorite.click(timeout=4_000)
                page.wait_for_timeout(800)
        except Exception:
            pass

        text = self._body_text(page)
        page_matches = self._store_context_in_text(text, location)
        self.run_meta["store_page_verified"] = page_matches
        self.run_meta["store_url"] = store_url
        return page_matches

    @staticmethod
    def _infer_brand(product: str | None) -> str | None:
        if not product:
            return None
        known = [
            # Cuidado bucal
            "Colgate", "Oral-B", "Sensodyne", "Listerine", "Crest", "Equate",
            "Corega", "Aquafresh", "Gum", "Curaprox", "Philips", "Marvis",
            # Cuidado de la ropa
            "Ariel", "Persil", "Roma", "Suavitel", "Vanish", "Cloralex", "Clorox",
            "Zote", "Ace", "Bold", "Blanca Nieves", "MAS", "Lysol", "Oxiclean",
            "Dr. Beckmann", "Ensueño", "Great Value", "Arm & Hammer", "Tide",
        ]
        lower = product.lower()
        for brand in known:
            if brand.lower() in lower:
                return brand
        return None

    def _extract_cards(self, page: Page, category: Category, location: Location) -> list[dict[str, Any]]:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        js = r"""
        anchors => {
          const money = /\$\s*[\d,.]+/;
          const seen = new Set();
          const out = [];
          for (const a of anchors) {
            const href = a.getAttribute('href');
            if (!href || seen.has(href)) continue;
            seen.add(href);

            let card = a;
            for (let i = 0; i < 10 && card && card.parentElement; i++) {
              const txt = (card.textContent || '').trim();
              if (money.test(txt) && txt.length > 20) break;
              card = card.parentElement;
            }
            if (!card) continue;
            const text = (card.textContent || '').replace(/\s+/g, ' ').trim();
            if (!money.test(text)) continue;

            const img = card.querySelector('img[alt]');
            const anchorText = (a.textContent || '').replace(/\s+/g, ' ').trim();
            const aria = a.getAttribute('aria-label');
            const product = (aria && aria.length > 4 ? aria : null) ||
                            (anchorText && anchorText.length > 4 ? anchorText : null) ||
                            (img ? img.getAttribute('alt') : null);
            out.push({href, product, text});
          }
          return out;
        }
        """
        anchors = page.locator('a[href*="/ip/"]')
        if not anchors.count():
            return []
        raw = anchors.evaluate_all(js)
        rows: list[dict[str, Any]] = []

        for item in raw:
            url = absolute_url(item.get("href"), BASE_URL)
            sku = extract_sku(url)
            product = clean_text(item.get("product"))
            text = clean_text(item.get("text")) or ""
            if not sku or not product:
                continue

            current_match = re.search(r"precio\s+actual\s*(?:MXN)?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", text, re.I)
            before_match = re.search(r"(?:Antes|costaba)\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", text, re.I)
            current = parse_money(current_match.group(1)) if current_match else None
            regular = parse_money(before_match.group(1)) if before_match else None

            if current is None:
                tokens = re.findall(r"\$\s*[\d,]+(?:\.\d{1,2})?", text)
                values = [parse_money(x) for x in tokens]
                values = [x for x in values if x is not None]
                current = values[0] if values else None
                regular = regular or (values[1] if len(values) > 1 else current)
            if regular is None:
                regular = current

            pickup = bool(re.search(r"\b(pickup|recoge|recoger|recogida)\b", text, re.I))
            if self.store_only and not pickup:
                continue

            promos = []
            for pattern in (
                r"Rebaja",
                r"Precio en línea",
                r"Más vendido",
                r"Combina\s+\d+\s*x\s*\$[\d,.]+",
                r"Ahorra\s*\$[\d,.]+",
            ):
                m = re.search(pattern, text, re.I)
                if m:
                    promos.append(m.group(0))

            rows.append(
                {
                    "scrape_timestamp": now,
                    "retailer": "Walmart",
                    "city": location.city,
                    "state": location.state,
                    "postal_code": location.postal_code,
                    "store": location.store,
                    "store_id": location.store_id,
                    "department": category.department,
                    "category": category.name,
                    "subcategory": category.subcategory,
                    "sub_subcategory": category.sub_subcategory,
                    "category_id": category.id,
                    "sku": sku,
                    "brand": self._infer_brand(product),
                    "product": product,
                    "price_current": current,
                    "price_regular": regular,
                    "promotion": clean_text(" | ".join(dict.fromkeys(promos))),
                    "pickup_available": pickup,
                    "store_context_verified": True,
                    "url": url,
                    "price_raw": text,
                }
            )
        return rows

    def scrape_category(self, category: Category, location: Location) -> list[dict[str, Any]]:
        if self.require_store_context and (not location.store_id or not location.store):
            raise WalmartStoreContextError("Walmart requiere una tienda configurada para esta corrida.")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context: BrowserContext = browser.new_context(
                locale="es-MX",
                viewport={"width": 1440, "height": 1000},
            )
            page = context.new_page()
            try:
                self._establish_store_context(page, location)
                rows: list[dict[str, Any]] = []
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
                            f"No se pudo verificar {location.store} / {location.postal_code} en Walmart. "
                            "Se guardaron diagnósticos; no se etiquetaron precios nacionales como precios de tienda."
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

                    if not new_rows:
                        no_new_pages += 1
                    else:
                        no_new_pages = 0
                    if no_new_pages >= 2:
                        break

                self.run_meta["store_context_verified"] = verified_any_page
                self.run_meta["unique_products"] = len(seen_skus)
                self._save_diagnostics(page, f"success_{category.id}_{location.id}")
                return rows
            finally:
                context.close()
                browser.close()
