from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from ..config import Category, Location
from ..parsers import clean_text


BASE_URL = "https://www.farmaciasanpablo.com.mx/"
DIAGNOSTICS = Path("diagnostics")


class FarmaciasSanPabloBlocked(RuntimeError):
    pass


class FarmaciasSanPabloNetworkUnavailable(RuntimeError):
    pass


class FarmaciasSanPabloScraper:
    """Scraper del catálogo online de Farmacias San Pablo.

    La categoría se usa para descubrir todo el surtido paginado. Cuando existe
    una URL individual `/p/`, la ficha de producto es la fuente preferida para
    SKU y precio; `h3.priceTotal` es el selector principal del precio vigente.
    Si no se solicitó una sucursal, las filas quedan como catálogo online sin
    contexto físico verificado.
    """

    MONEY_RE = re.compile(r"\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)")
    PRODUCT_PATH_RE = re.compile(r"/p/(\d+)(?:$|[/?#])", re.IGNORECASE)
    ID_RE = re.compile(r"\b(?:SKU|UPC|EAN|Código|Codigo)\s*[:#-]?\s*(\d{6,14})\b", re.IGNORECASE)
    BRAND_CANDIDATES = [
        "Afrin", "Arm & Hammer", "Bexident", "Colgate", "Corega", "Curaprox",
        "Dentaflox", "Durex", "Fluoxytil", "Fullsen", "GUM", "Lacer",
        "Listerine", "NeilMed", "Oral-B", "Parodontax", "Playboy", "Prudence",
        "Real Sea", "Rinomar", "Sensodyne", "Sico", "Sinomarin", "Stérimar",
        "Sterimar", "Trojan", "Vantal", "Xerolacer",
    ]

    def __init__(self, headless: bool = True, max_pages: int = 100) -> None:
        self.headless = headless
        self.max_pages = max_pages

    @staticmethod
    def _page_url(url: str, page_number: int) -> str:
        if page_number <= 1:
            return url
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        query["currentPage"] = [str(page_number)]
        encoded = urlencode([(k, v) for k, values in query.items() for v in values])
        return urlunparse(parsed._replace(query=encoded))

    @staticmethod
    def _is_blocked(status: int | None, title: str | None, body: str | None) -> bool:
        blob = f"{title or ''}\n{body or ''}".casefold()
        return bool(
            status == 403
            or "access denied" in blob
            or "you don't have permission to access" in blob
            or "request rejected" in blob
            or "verify you are human" in blob
            or "verifica que eres humano" in blob
        )

    @classmethod
    def _parse_money_values(cls, text: str | None) -> list[float]:
        values: list[float] = []
        for raw in cls.MONEY_RE.findall(text or ""):
            try:
                value = float(raw.replace(",", ""))
            except ValueError:
                continue
            if value > 0:
                values.append(value)
        return values

    @classmethod
    def _prices_from_text(cls, text: str | None) -> tuple[float | None, float | None, str | None]:
        values = cls._parse_money_values(text)
        if not values:
            return None, None, None
        current = min(values)
        regular = max(values)
        promo_parts: list[str] = []
        discount = re.search(r"\b(\d{1,2})\s*%\s*(?:de\s*)?descuento\b", text or "", re.IGNORECASE)
        if discount:
            promo_parts.append(f"{discount.group(1)}% de descuento")
        if current < regular:
            promo_parts.append("Precio promocional")
        return current, regular, " | ".join(dict.fromkeys(promo_parts)) if promo_parts else None

    @classmethod
    def _sku_from_url(cls, url: str | None) -> str | None:
        if not url:
            return None
        match = cls.PRODUCT_PATH_RE.search(url)
        if not match:
            return None
        raw = match.group(1)
        stripped = raw.lstrip("0")
        return stripped or raw

    @classmethod
    def _infer_brand(cls, product: str | None, text: str | None = None) -> str | None:
        blob = clean_text(f"{product or ''} {text or ''}") or ""
        for brand in cls.BRAND_CANDIDATES:
            if re.search(rf"(?<!\w){re.escape(brand)}(?!\w)", blob, re.IGNORECASE):
                return "Stérimar" if brand.casefold() == "sterimar" else brand
        return None

    @staticmethod
    def _extract_json_ld(page) -> dict:
        try:
            scripts = page.locator('script[type="application/ld+json"]').all_text_contents()
        except Exception:
            return {}
        for raw in scripts:
            try:
                data = json.loads(raw)
            except Exception:
                continue
            candidates = data if isinstance(data, list) else [data]
            for item in candidates:
                if isinstance(item, dict) and str(item.get("@type", "")).casefold() == "product":
                    return item
                if isinstance(item, dict) and isinstance(item.get("@graph"), list):
                    for child in item["@graph"]:
                        if isinstance(child, dict) and str(child.get("@type", "")).casefold() == "product":
                            return child
        return {}

    @classmethod
    def _sku_from_product_page(cls, page, product_url: str) -> str | None:
        data = cls._extract_json_ld(page)
        for key in ("gtin13", "gtin12", "gtin", "sku", "mpn"):
            value = clean_text(str(data.get(key))) if data.get(key) is not None else None
            if value and re.fullmatch(r"\d{6,14}", value):
                return value
        selectors = [
            'meta[itemprop="sku"]', 'meta[itemprop="gtin13"]', '[itemprop="sku"]',
            '[data-sku]', '[data-product-code]', '.product-code', '.sku',
        ]
        for selector in selectors:
            try:
                loc = page.locator(selector).first
                if loc.count() == 0:
                    continue
                value = loc.get_attribute("content") or loc.get_attribute("data-sku") or loc.get_attribute("data-product-code") or loc.inner_text(timeout=1000)
                match = re.search(r"\b(\d{6,14})\b", value or "")
                if match:
                    return match.group(1)
            except Exception:
                continue
        try:
            text = page.locator("body").inner_text(timeout=5000)
        except Exception:
            text = ""
        match = cls.ID_RE.search(text)
        if match:
            return match.group(1)
        return cls._sku_from_url(product_url)

    @classmethod
    def _product_detail(cls, page, product_url: str) -> dict:
        response = page.goto(product_url, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(1200)
        status = response.status if response else None
        title = page.title()
        body = page.locator("body").inner_text(timeout=10_000)
        if cls._is_blocked(status, title, body):
            raise FarmaciasSanPabloBlocked("Farmacias San Pablo devolvió Access Denied/403")
        if status and status >= 400:
            raise RuntimeError(f"HTTP {status} en ficha {product_url}")

        data = cls._extract_json_ld(page)
        product = clean_text(str(data.get("name"))) if data.get("name") else None
        if not product:
            for selector in ("h1", ".name", ".product-name"):
                try:
                    candidate = clean_text(page.locator(selector).first.inner_text(timeout=1500))
                    if candidate:
                        product = candidate
                        break
                except Exception:
                    pass

        brand = None
        raw_brand = data.get("brand")
        if isinstance(raw_brand, dict):
            brand = clean_text(str(raw_brand.get("name"))) if raw_brand.get("name") else None
        elif raw_brand:
            brand = clean_text(str(raw_brand))
        brand = brand or cls._infer_brand(product, body)

        current = None
        price_raw = None
        try:
            price_node = page.locator("h3.priceTotal").first
            if price_node.count() > 0:
                price_raw = clean_text(price_node.inner_text(timeout=5000))
                vals = cls._parse_money_values(price_raw)
                current = vals[0] if vals else None
        except Exception:
            pass

        regular = current
        promo = None
        if current is not None:
            snippets: list[str] = [price_raw or ""]
            for selector in (".price-old", ".old-price", ".price-before", "del", "s"):
                try:
                    for value in page.locator(selector).all_inner_texts()[:10]:
                        if "$" in value:
                            snippets.append(value)
                except Exception:
                    pass
            price_context = " | ".join(snippets)
            values = cls._parse_money_values(price_context)
            if values:
                regular = max([current, *values])
            if regular and current < regular:
                pct = round((1 - current / regular) * 100)
                promo = f"{pct}% de descuento | Precio promocional"
        else:
            offers = data.get("offers")
            if isinstance(offers, dict):
                try:
                    current = float(offers.get("price")) if offers.get("price") is not None else None
                except (TypeError, ValueError):
                    current = None
                regular = current
                if current is not None:
                    price_raw = json.dumps(offers, ensure_ascii=False)

        return {
            "sku": cls._sku_from_product_page(page, product_url),
            "brand": brand,
            "product": product,
            "price_current": current,
            "price_regular": regular,
            "promotion": promo,
            "url": product_url,
            "price_raw": price_raw,
        }

    @staticmethod
    def _category_cards(page) -> list[dict]:
        return page.locator("body").evaluate(
            """
            () => {
              const out = [];
              const seen = new Set();
              const nodes = Array.from(document.querySelectorAll('a[href*="/p/"], [data-product-code], [data-product-id], [data-code]'));
              for (const node of nodes) {
                let card = node;
                for (let i = 0; i < 7 && card && !(card.innerText || '').includes('$'); i++) card = card.parentElement;
                card = card || node;
                const hrefNode = node.matches('a[href*="/p/"]') ? node : card.querySelector('a[href*="/p/"]');
                const href = hrefNode ? hrefNode.href : '';
                const code = node.getAttribute('data-product-code') || node.getAttribute('data-product-id') || node.getAttribute('data-code') || card.getAttribute('data-product-code') || card.getAttribute('data-product-id') || '';
                const text = (card.innerText || '').trim();
                const key = href || code || text.slice(0,120);
                if (!key || seen.has(key) || !text) continue;
                seen.add(key);
                const title = (hrefNode && (hrefNode.getAttribute('title') || hrefNode.getAttribute('aria-label') || hrefNode.innerText)) || '';
                out.push({href, code, title:(title||'').trim(), text});
              }
              return out;
            }
            """
        )

    def _discover_products(self, page, category: Category) -> tuple[list[str], list[dict]]:
        urls: dict[str, None] = {}
        fallback_cards: dict[str, dict] = {}
        empty_rounds = 0

        for page_number in range(1, self.max_pages + 1):
            target = self._page_url(category.url, page_number)
            try:
                response = page.goto(target, wait_until="domcontentloaded", timeout=45_000)
            except PlaywrightError as exc:
                raise FarmaciasSanPabloNetworkUnavailable(str(exc)) from exc
            page.wait_for_timeout(1800)
            status = response.status if response else None
            title = page.title()
            body = page.locator("body").inner_text(timeout=10_000)
            if self._is_blocked(status, title, body):
                raise FarmaciasSanPabloBlocked("Farmacias San Pablo devolvió Access Denied/403")
            if status and status >= 400:
                raise RuntimeError(f"HTTP {status} en {target}")

            cards = self._category_cards(page)
            before_urls = len(urls)
            before_cards = len(fallback_cards)
            for card in cards:
                href = clean_text(card.get("href"))
                if href and "/p/" in href:
                    urls[urljoin(BASE_URL, href)] = None
                else:
                    code = clean_text(card.get("code"))
                    text = clean_text(card.get("text"))
                    if code and text and "$" in text:
                        fallback_cards[code] = card

            if len(urls) == before_urls and len(fallback_cards) == before_cards:
                empty_rounds += 1
            else:
                empty_rounds = 0
            if empty_rounds >= 2:
                break

            # Si la página no expone control siguiente y ya hubo al menos una página útil,
            # evitamos fabricar páginas inexistentes.
            try:
                next_visible = page.locator('a[rel="next"], .pagination-next:not(.disabled), a:has-text("Siguiente")').count() > 0
            except Exception:
                next_visible = False
            if page_number >= 2 and not next_visible and cards and len(cards) < 20:
                break

        return list(urls), list(fallback_cards.values())

    def _fallback_rows(self, cards: list[dict], category: Category, location: Location, now: str) -> list[dict]:
        rows: list[dict] = []
        for card in cards:
            text = clean_text(card.get("text"))
            code = clean_text(card.get("code"))
            if not code or not text:
                continue
            current, regular, promotion = self._prices_from_text(text)
            if current is None:
                continue
            title = clean_text(card.get("title"))
            if not title:
                lines = [clean_text(x) for x in text.splitlines()]
                title = next((x for x in lines if x and "$" not in x and len(x) > 5), None)
            if not title:
                continue
            rows.append({
                "scrape_timestamp": now,
                "retailer": "Farmacias San Pablo",
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
                "sku": code,
                "brand": self._infer_brand(title, text),
                "product": title,
                "price_current": current,
                "price_regular": regular,
                "promotion": promotion,
                "pickup_available": None,
                "store_context_verified": False,
                "store_context_method": "online_catalog_category_card_fallback",
                "url": category.url,
                "price_raw": text,
            })
        return rows

    def scrape_category(self, category: Category, location: Location) -> list[dict]:
        DIAGNOSTICS.mkdir(parents=True, exist_ok=True)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context(
                locale="es-MX",
                viewport={"width": 1440, "height": 1000},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()
            try:
                try:
                    product_urls, fallback_cards = self._discover_products(page, category)
                except (FarmaciasSanPabloBlocked, FarmaciasSanPabloNetworkUnavailable) as exc:
                    meta = {
                        "retailer": "Farmacias San Pablo",
                        "category_id": category.id,
                        "url": category.url,
                        "status": "BLOCKED" if isinstance(exc, FarmaciasSanPabloBlocked) else "NETWORK_UNAVAILABLE",
                        "error": str(exc),
                    }
                    (DIAGNOSTICS / f"farmacias_san_pablo_{category.id}.json").write_text(
                        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    raise

                rows: list[dict] = []
                for product_url in product_urls:
                    detail = self._product_detail(page, product_url)
                    if not detail.get("sku") or not detail.get("product") or detail.get("price_current") is None:
                        continue
                    rows.append({
                        "scrape_timestamp": now,
                        "retailer": "Farmacias San Pablo",
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
                        "sku": detail["sku"],
                        "brand": detail["brand"],
                        "product": detail["product"],
                        "price_current": detail["price_current"],
                        "price_regular": detail["price_regular"],
                        "promotion": detail["promotion"],
                        "pickup_available": None,
                        "store_context_verified": False,
                        "store_context_method": "online_catalog_product_detail_h3_priceTotal",
                        "url": detail["url"],
                        "price_raw": detail["price_raw"],
                    })

                if not product_urls:
                    rows.extend(self._fallback_rows(fallback_cards, category, location, now))

                unique = {(str(row["sku"]), row["url"]): row for row in rows}
                rows = list(unique.values())
                meta = {
                    "retailer": "Farmacias San Pablo",
                    "category_id": category.id,
                    "category_url": category.url,
                    "product_urls_discovered": len(product_urls),
                    "fallback_cards_discovered": len(fallback_cards),
                    "rows": len(rows),
                    "store_context": "online_catalog_no_store_requested",
                }
                (DIAGNOSTICS / f"farmacias_san_pablo_{category.id}.json").write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                return rows
            finally:
                context.close()
                browser.close()


__all__ = [
    "FarmaciasSanPabloBlocked",
    "FarmaciasSanPabloNetworkUnavailable",
    "FarmaciasSanPabloScraper",
]
