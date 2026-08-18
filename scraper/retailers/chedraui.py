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

BASE_URL = "https://www.chedraui.com.mx/"
PRODUCT_SELECTOR = 'a[href$="/p"], a[href*="/p?"]'
BLOCK_MARKERS = (
    "access denied",
    "forbidden",
    "captcha",
    "robot or human",
    "verifica tu identidad",
    "verify you are human",
)


class ChedrauiBlocked(RuntimeError):
    pass


class ChedrauiStoreContextError(RuntimeError):
    pass


class ChedrauiScraper:
    """Scraper browser-based para el catálogo público de Chedraui.

    El scraper usa la navegación normal del sitio para seleccionar Pickup en la
    tienda configurada. Nunca atribuye datos a una tienda si el contexto de
    tienda no puede verificarse y no intenta resolver CAPTCHAs o desafíos.
    """

    def __init__(
        self,
        headless: bool = True,
        diagnostics_dir: str | Path = "diagnostics",
        max_pages: int = 100,
        wait_ms: int = 900,
        require_store_context: bool = True,
    ) -> None:
        self.headless = headless
        self.diagnostics_dir = Path(diagnostics_dir)
        self.diagnostics_dir.mkdir(parents=True, exist_ok=True)
        self.max_pages = max_pages
        self.wait_ms = wait_ms
        self.require_store_context = require_store_context
        self.run_meta: dict[str, Any] = {}
        self._active_store_context_method: str | None = None

    @staticmethod
    def _normalize(value: str | None) -> str:
        if not value:
            return ""
        value = value.lower()
        value = (
            value.replace("á", "a")
            .replace("é", "e")
            .replace("í", "i")
            .replace("ó", "o")
            .replace("ú", "u")
            .replace("ü", "u")
        )
        return re.sub(r"\s+", " ", value).strip()

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
            page.screenshot(path=str(self.diagnostics_dir / f"chedraui_{safe}.png"), full_page=True)
        except Exception:
            pass
        try:
            (self.diagnostics_dir / f"chedraui_{safe}.html").write_text(page.content(), encoding="utf-8")
        except Exception:
            pass
        try:
            (self.diagnostics_dir / "chedraui_run_meta.json").write_text(
                json.dumps(self.run_meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    def _body_text(self, page: Page) -> str:
        try:
            return page.locator("body").inner_text(timeout=8_000)
        except Exception:
            return ""

    def _assert_not_blocked(self, page: Page, status: int | None = None) -> None:
        body = self._normalize(self._body_text(page))
        if status in (401, 403, 429) or any(self._normalize(x) in body for x in BLOCK_MARKERS):
            self._save_diagnostics(page, "blocked")
            raise ChedrauiBlocked(
                "Chedraui bloqueó o desafió la sesión. Se guardaron diagnósticos; "
                "el scraper no intenta evadir la protección del sitio."
            )

    def _browser_state_blob(self, page: Page) -> str:
        payload: dict[str, Any] = {"cookies": [], "localStorage": {}, "sessionStorage": {}}
        try:
            payload["cookies"] = page.context.cookies()
        except Exception:
            pass
        try:
            payload.update(
                page.evaluate(
                    """
                    () => ({
                      localStorage: Object.fromEntries(Object.entries(localStorage)),
                      sessionStorage: Object.fromEntries(Object.entries(sessionStorage)),
                    })
                    """
                )
            )
        except Exception:
            pass
        return json.dumps(payload, ensure_ascii=False)

    @classmethod
    def _store_context_in_text(cls, text: str, location: Location) -> bool:
        normalized = cls._normalize(text)
        store_id = cls._normalize(location.store_id)
        postal = cls._normalize(location.postal_code)
        polanco = "polanco" in normalized
        strong_detail = bool(
            (store_id and store_id in normalized)
            or (postal and postal in normalized)
            or "selecto mexico polanco" in normalized
            or "selecto méxico polanco" in text.lower()
        )
        pickup_detail = bool(
            re.search(r"(recoger|pickup).{0,120}polanco", normalized)
            or re.search(r"polanco.{0,120}(recoger|pickup)", normalized)
        )
        return polanco and (strong_detail or pickup_detail)

    @classmethod
    def _store_context_in_state_blob(cls, blob: str, location: Location) -> bool:
        normalized = cls._normalize(blob)
        hits = 0
        if "polanco" in normalized:
            hits += 1
        if location.store_id and cls._normalize(location.store_id) in normalized:
            hits += 1
        if location.postal_code and cls._normalize(location.postal_code) in normalized:
            hits += 1
        if "selecto" in normalized:
            hits += 1
        return hits >= 2 and "polanco" in normalized

    def _verify_store_context(self, page: Page, location: Location) -> tuple[bool, str | None]:
        if self._store_context_in_text(self._body_text(page), location):
            return True, "page_text"
        if self._store_context_in_state_blob(self._browser_state_blob(page), location):
            return True, "browser_state"
        return False, None

    def _try_select_store_ui(self, page: Page, location: Location) -> tuple[bool, str | None]:
        # Flujo documentado por Chedraui: Agregar dirección -> Recoger en -> tienda.
        for label in ("Agregar dirección", "Agrega dirección", "Agregar direccion", "Agrega direccion"):
            try:
                node = page.get_by_text(label, exact=False).first
                if node.count() and node.is_visible():
                    node.click(timeout=5_000)
                    page.wait_for_timeout(700)
                    break
            except Exception:
                continue

        for label in ("Recoger en", "Recoger en tienda", "Pickup", "Recoger"):
            try:
                node = page.get_by_text(label, exact=False).first
                if node.count() and node.is_visible():
                    node.click(timeout=5_000)
                    page.wait_for_timeout(700)
                    break
            except Exception:
                continue

        for label in ("Directorio de tiendas", "Directorio", "Ver tiendas"):
            try:
                node = page.get_by_text(label, exact=False).first
                if node.count() and node.is_visible():
                    node.click(timeout=5_000)
                    page.wait_for_timeout(700)
                    break
            except Exception:
                continue

        # Busca Polanco en cualquier input visible relacionado con dirección/tienda.
        try:
            inputs = page.locator("input")
            for i in range(min(inputs.count(), 50)):
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
                    if any(x in hint for x in ("direc", "tienda", "buscar", "ubic", "postal", "codigo")):
                        inp.fill("Polanco")
                        page.wait_for_timeout(1_200)
                        break
                except Exception:
                    continue
        except Exception:
            pass

        for label in (
            location.store or "Chedraui Selecto México Polanco",
            "Selecto México Polanco",
            "Selecto Mexico Polanco",
            "Polanco",
        ):
            try:
                node = page.get_by_text(label, exact=False).first
                if node.count() and node.is_visible():
                    node.click(timeout=5_000)
                    page.wait_for_timeout(900)
                    break
            except Exception:
                continue

        for label in ("Seleccionar", "Elegir", "Usar esta tienda", "Confirmar", "Guardar"):
            try:
                node = page.get_by_text(label, exact=False).first
                if node.count() and node.is_visible():
                    node.click(timeout=4_000)
                    page.wait_for_timeout(900)
                    break
            except Exception:
                continue

        verified, method = self._verify_store_context(page, location)
        if verified:
            return True, f"{method}_after_ui"
        return False, None

    @staticmethod
    def _infer_brand(product: str | None) -> str | None:
        if not product:
            return None
        known = [
            "Colgate", "Oral-B", "Listerine", "Sensodyne", "Crest", "GUM", "Corega",
            "Aquafresh", "Bexident", "Curaprox", "Philips", "Condor",
            "Suavitel", "Downy", "Ariel", "Ensueño", "Vanish", "Cloralex", "Ace",
            "MÁS", "Mas Color", "Roma", "Zote", "Persil", "Tide", "Dr. Beckmann",
            "Blanca Nieves", "Carisma", "Bold", "Oxiclean", "Arm & Hammer",
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
          const out = [];
          const seen = new Set();
          const pickText = (root, selectors) => {
            for (const sel of selectors) {
              const node = root.querySelector(sel);
              if (node && money.test(node.textContent || '')) return (node.textContent || '').trim();
            }
            return null;
          };
          for (const a of anchors) {
            const href = a.getAttribute('href');
            if (!href || seen.has(href) || !/\/p(?:\?|$)/i.test(href)) continue;
            seen.add(href);

            let card = a;
            for (let i = 0; i < 10 && card && card.parentElement; i++) {
              const txt = (card.textContent || '').replace(/\s+/g, ' ').trim();
              if (money.test(txt) && txt.length > 20 && txt.length < 3000) break;
              card = card.parentElement;
            }
            if (!card) continue;
            const cardText = (card.textContent || '').replace(/\s+/g, ' ').trim();
            if (!money.test(cardText)) continue;

            const img = card.querySelector('img[alt]');
            const heading = card.querySelector('h2, h3, [class*="productName"], [class*="product-name"]');
            const product =
              a.getAttribute('aria-label') ||
              a.getAttribute('title') ||
              (heading ? (heading.textContent || '').trim() : null) ||
              (img ? img.getAttribute('alt') : null) ||
              (a.textContent || '').trim();

            const selling = pickText(card, [
              '[class*="sellingPriceValue"]', '[class*="sellingPrice"]',
              '[class*="selling-price"]', '[data-testid*="selling"]'
            ]);
            const regular = pickText(card, [
              '[class*="listPriceValue"]', '[class*="listPrice"]',
              '[class*="list-price"]', '[data-testid*="list"]'
            ]);
            const priceNodes = Array.from(card.querySelectorAll('[class*="price"], [data-testid*="price"]'))
              .map(n => (n.textContent || '').trim())
              .filter(t => money.test(t));
            const promoNodes = Array.from(card.querySelectorAll('[class*="promo"], [class*="discount"], [class*="badge"]'))
              .map(n => (n.textContent || '').trim())
              .filter(Boolean);

            out.push({
              href,
              product,
              selling,
              regular,
              price_texts: [...new Set(priceNodes)],
              promo_texts: [...new Set(promoNodes)],
              card_text: cardText
            });
          }
          return out;
        }
        """
        locator = page.locator(PRODUCT_SELECTOR)
        if not locator.count():
            return []
        raw = locator.evaluate_all(js)
        rows: list[dict[str, Any]] = []
        for item in raw:
            url = absolute_url(item.get("href"), BASE_URL)
            product = clean_text(item.get("product"))
            if not product or not url:
                continue

            selling = parse_money(item.get("selling"))
            regular = parse_money(item.get("regular"))
            fallback_values: list[float] = []
            for text in item.get("price_texts") or [item.get("card_text")]:
                for token in re.findall(r"\$\s*[\d,]+(?:\.\d{1,2})?", text or ""):
                    value = parse_money(token)
                    if value is not None and value not in fallback_values:
                        fallback_values.append(value)

            current_price = selling if selling is not None else (fallback_values[0] if fallback_values else None)
            if regular is not None:
                regular_price = regular
            elif len(fallback_values) > 1:
                regular_price = fallback_values[1]
            else:
                regular_price = current_price
            if current_price and regular_price and current_price > regular_price:
                current_price, regular_price = regular_price, current_price

            rows.append(
                {
                    "scrape_timestamp": now,
                    "retailer": "Chedraui",
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
                    "sku": extract_sku(url),
                    "brand": self._infer_brand(product),
                    "product": product,
                    "price_current": current_price,
                    "price_regular": regular_price,
                    "promotion": clean_text(" | ".join(item.get("promo_texts") or [])),
                    "pickup_available": True,
                    "store_context_verified": True,
                    "store_context_method": self._active_store_context_method,
                    "url": url,
                    "price_raw": clean_text(" | ".join(item.get("price_texts") or [])),
                }
            )
        return rows

    def _collect_pages(self, page: Page, category: Category, location: Location) -> list[dict[str, Any]]:
        rows_by_key: dict[str, dict[str, Any]] = {}
        stale_pages = 0
        max_pages = self.max_pages if self.max_pages > 0 else 100

        for page_number in range(1, max_pages + 1):
            url = self._paged_url(category.url, page_number)
            response = page.goto(url, wait_until="domcontentloaded", timeout=120_000)
            self._assert_not_blocked(page, response.status if response else None)
            page.wait_for_timeout(self.wait_ms)
            try:
                page.wait_for_selector(PRODUCT_SELECTOR, timeout=20_000)
            except PlaywrightTimeoutError:
                if page_number == 1:
                    self._save_diagnostics(page, f"no_products_{category.id}")
                break

            page_rows = self._extract_cards(page, category, location)
            before = len(rows_by_key)
            for row in page_rows:
                key = str(row.get("sku") or row.get("url"))
                if key:
                    rows_by_key[key] = row
            new_count = len(rows_by_key) - before
            self.run_meta.setdefault("pages", []).append(
                {"page": page_number, "url": page.url, "rows": len(page_rows), "new": new_count}
            )

            if new_count == 0:
                stale_pages += 1
            else:
                stale_pages = 0
            if stale_pages >= 2:
                break

        return list(rows_by_key.values())

    def scrape_category(self, category: Category, location: Location) -> list[dict[str, Any]]:
        if self.require_store_context and (not location.store_id or not location.store):
            raise ChedrauiStoreContextError("Chedraui requiere una tienda configurada para esta corrida.")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context: BrowserContext = browser.new_context(
                locale="es-MX",
                viewport={"width": 1440, "height": 1000},
            )
            page = context.new_page()
            try:
                response = page.goto(category.url, wait_until="domcontentloaded", timeout=120_000)
                self._assert_not_blocked(page, response.status if response else None)
                page.wait_for_timeout(1_500)

                verified, method = self._verify_store_context(page, location)
                if not verified:
                    verified, method = self._try_select_store_ui(page, location)
                if self.require_store_context and not verified:
                    self.run_meta["store_context_verified"] = False
                    self._save_diagnostics(page, "store_context_error")
                    raise ChedrauiStoreContextError(
                        "No fue posible verificar Chedraui Selecto México Polanco (tienda 232)."
                    )

                self._active_store_context_method = method
                self.run_meta["store_context_verified"] = True
                self.run_meta["store_context_method"] = method
                self.run_meta["store_id"] = location.store_id
                self.run_meta["store"] = location.store

                rows = self._collect_pages(page, category, location)
                self._save_diagnostics(page, f"success_{category.id}")
                return rows
            finally:
                context.close()
                browser.close()
