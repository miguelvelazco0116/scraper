from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from ..config import Category, Location
from ..parsers import clean_text


BASE_URL = "https://www.fahorro.com/"
DIAGNOSTICS = Path("diagnostics")


class FarmaciasDelAhorroBlocked(RuntimeError):
    pass


class FarmaciasDelAhorroNetworkUnavailable(RuntimeError):
    pass


class FarmaciasDelAhorroScraper:
    """Extractor del catálogo público de Farmacias del Ahorro.

    El contexto inicial es catálogo online nacional. No se atribuye una fila a
    una sucursal física si el usuario no configuró una tienda concreta.
    """

    MONEY_RE = re.compile(r"(?:MXN\s*)?\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)", re.IGNORECASE)
    SKU_PATTERNS = [
        re.compile(r'itemprop=["\']sku["\'][^>]*content=["\']([^"\']+)', re.IGNORECASE),
        re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+itemprop=["\']sku["\']', re.IGNORECASE),
        re.compile(r'itemprop=["\']sku["\'][^>]*>\s*([^<]+)', re.IGNORECASE),
        re.compile(r'"sku"\s*:\s*"([^"]+)"', re.IGNORECASE),
        re.compile(r'\bSKU\b\s*</[^>]+>\s*<[^>]+>\s*([^<]+)', re.IGNORECASE),
    ]

    def __init__(self, headless: bool = True, max_pages: int = 100) -> None:
        self.headless = headless
        self.max_pages = max_pages

    @classmethod
    def _parse_money(cls, value: str | None) -> float | None:
        if not value:
            return None
        match = cls.MONEY_RE.search(value)
        if not match:
            return None
        try:
            return float(match.group(1).replace(",", ""))
        except ValueError:
            return None

    @classmethod
    def _prices_from_card(
        cls,
        final_text: str | None,
        old_text: str | None,
        card_text: str | None,
    ) -> tuple[float | None, float | None, str | None]:
        current = cls._parse_money(final_text)
        regular = cls._parse_money(old_text)
        text = clean_text(card_text) or ""

        values: list[float] = []
        for raw in cls.MONEY_RE.findall(text):
            try:
                values.append(float(raw.replace(",", "")))
            except ValueError:
                pass

        if current is None and values:
            current = values[-1]
        if regular is None:
            regular = max(values) if values else current
        if current is not None and regular is not None and current > regular:
            current, regular = regular, current

        promo: list[str] = []
        percent = re.search(r"\b(\d{1,2})\s*%\s*(?:de\s*)?descuento\b", text, re.IGNORECASE)
        if percent:
            promo.append(f"{percent.group(1)}% de descuento")
        if current is not None and regular is not None and current < regular:
            promo.append("Precio promocional")
        promotion = " | ".join(dict.fromkeys(promo)) if promo else None
        return current, regular, promotion

    @staticmethod
    def _infer_brand(product: str | None, text: str | None = None) -> str | None:
        blob = clean_text(text) or clean_text(product) or ""
        brands = [
            "Sterimar", "Afrin", "Iliadin", "Neilmed", "Sinomarin", "Vick",
            "Durex", "Sico", "Prudence", "Trojan", "Playboy", "M Force",
            "Colgate", "Oral-B", "Sensodyne", "Listerine", "Bexident", "Gum",
            "Elmex", "Parodontax", "Curaprox", "Aquafresh", "Closeup",
        ]
        for brand in brands:
            if re.search(rf"(?<!\w){re.escape(brand)}(?!\w)", blob, re.IGNORECASE):
                return brand
        return None

    @staticmethod
    def _page_url(url: str, page_number: int) -> str:
        if page_number <= 1:
            return url
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["p"] = str(page_number)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    @staticmethod
    def _assert_not_blocked(page) -> None:
        try:
            text = (page.locator("body").inner_text(timeout=10_000) or "").casefold()
        except Exception:
            return
        markers = [
            "access denied",
            "request rejected",
            "verify you are human",
            "verifica que eres humano",
            "captcha",
        ]
        if any(marker in text for marker in markers):
            raise FarmaciasDelAhorroBlocked("Farmacias del Ahorro presentó un bloqueo o verificación")

    @staticmethod
    def _target_count(page) -> int | None:
        selectors = [".toolbar-amount", "#toolbar-amount", ".products.wrapper + .toolbar"]
        texts: list[str] = []
        for selector in selectors:
            try:
                locator = page.locator(selector)
                if locator.count():
                    texts.append(locator.first.inner_text(timeout=2_000))
            except Exception:
                pass
        for text in texts:
            matches = re.findall(r"(?:de|of)\s+(\d{1,5})\b", text, re.IGNORECASE)
            if matches:
                return max(int(x) for x in matches)
        return None

    @staticmethod
    def _extract_cards(page) -> list[dict]:
        return page.locator("li.product-item, .product-item-info").evaluate_all(
            """
            nodes => {
              const out = [];
              const seen = new Set();
              for (const node of nodes) {
                const root = node.matches('li.product-item') ? node : (node.closest('li.product-item') || node);
                const link = root.querySelector('a.product-item-link, a.product-item-photo, a[href$=".html"]');
                if (!link || !link.href || seen.has(link.href)) continue;
                const nameNode = root.querySelector('.product-item-name, .product-item-link, [class*="product-name"]');
                const name = (nameNode?.textContent || link.getAttribute('title') || link.textContent || '').trim();
                if (!name) continue;
                const finalNode = root.querySelector('[data-price-type="finalPrice"] .price, .special-price .price, .price-final_price .price, .price-box .price');
                const oldNode = root.querySelector('[data-price-type="oldPrice"] .price, .old-price .price');
                const skuNode = root.querySelector('[data-product-sku], form[data-product-sku]');
                const brandNode = root.querySelector('[class*="brand" i], [data-brand]');
                const idNode = root.querySelector('[data-product-id], form[data-product-id]');
                seen.add(link.href);
                out.push({
                  url: link.href,
                  name,
                  finalText: finalNode ? finalNode.textContent.trim() : '',
                  oldText: oldNode ? oldNode.textContent.trim() : '',
                  text: (root.innerText || '').trim(),
                  sku: skuNode ? (skuNode.getAttribute('data-product-sku') || '') : '',
                  brand: brandNode ? ((brandNode.getAttribute('data-brand') || brandNode.textContent || '').trim()) : '',
                  productId: idNode ? (idNode.getAttribute('data-product-id') || '') : ''
                });
              }
              return out;
            }
            """
        )

    @classmethod
    def _sku_from_html(cls, html: str) -> str | None:
        for pattern in cls.SKU_PATTERNS:
            match = pattern.search(html)
            if match:
                sku = clean_text(match.group(1))
                if sku:
                    return sku
        return None

    @staticmethod
    def _goto(page, url: str):
        last: Exception | None = None
        for attempt in range(1, 4):
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                page.wait_for_selector("body", state="attached", timeout=10_000)
                return response
            except (PlaywrightError, PlaywrightTimeoutError) as exc:
                last = exc
                if attempt < 3:
                    page.wait_for_timeout(attempt * 1_500)
        raise FarmaciasDelAhorroNetworkUnavailable(f"No fue posible cargar {url}: {last}")

    def _detail_sku(self, context, url: str) -> str | None:
        try:
            response = context.request.get(url, timeout=25_000)
            if not response.ok:
                return None
            return self._sku_from_html(response.text())
        except Exception:
            return None

    def scrape_category(self, category: Category, location: Location) -> list[dict]:
        DIAGNOSTICS.mkdir(parents=True, exist_ok=True)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        rows_by_url: dict[str, dict] = {}
        target: int | None = None
        pages_visited = 0
        sample_cards: list[dict] = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context(
                locale="es-MX",
                viewport={"width": 1440, "height": 1000},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/139.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()
            try:
                for page_number in range(1, self.max_pages + 1):
                    url = self._page_url(category.url, page_number)
                    response = self._goto(page, url)
                    pages_visited += 1
                    if response and response.status >= 400:
                        raise FarmaciasDelAhorroNetworkUnavailable(f"HTTP {response.status} en {url}")
                    self._assert_not_blocked(page)
                    try:
                        page.wait_for_selector("li.product-item, .product-item-info", timeout=20_000)
                    except Exception:
                        pass
                    page.wait_for_timeout(1_000)

                    if target is None:
                        target = self._target_count(page)
                    cards = self._extract_cards(page)
                    if len(sample_cards) < 10:
                        sample_cards.extend(cards[: 10 - len(sample_cards)])

                    new_count = 0
                    for card in cards:
                        product_url = urljoin(BASE_URL, card.get("url") or "")
                        if product_url in rows_by_url:
                            continue
                        product = clean_text(card.get("name"))
                        if not product:
                            continue
                        current, regular, promotion = self._prices_from_card(
                            card.get("finalText"), card.get("oldText"), card.get("text")
                        )
                        if current is None:
                            continue
                        brand = clean_text(card.get("brand")) or self._infer_brand(product, card.get("text"))
                        sku = clean_text(card.get("sku"))
                        rows_by_url[product_url] = {
                            "scrape_timestamp": now,
                            "retailer": "Farmacias del Ahorro",
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
                            "brand": brand,
                            "product": product,
                            "price_current": current,
                            "price_regular": regular,
                            "promotion": promotion,
                            "pickup_available": None,
                            "store_context_verified": False,
                            "store_context_method": "online_catalog_no_store_requested",
                            "url": product_url,
                            "price_raw": clean_text(card.get("text")),
                        }
                        new_count += 1

                    if target and len(rows_by_url) >= target:
                        break
                    if page_number > 1 and new_count == 0:
                        break
                    try:
                        next_link = page.locator("a.action.next, .pages-item-next a")
                        has_next = next_link.count() > 0 and next_link.first.is_visible(timeout=1_000)
                    except Exception:
                        has_next = False
                    if not has_next and target is None:
                        break

                missing = [row for row in rows_by_url.values() if not clean_text(row.get("sku"))]
                for row in missing:
                    row["sku"] = self._detail_sku(context, row["url"])

                meta = {
                    "category_id": category.id,
                    "url": category.url,
                    "target_products": target,
                    "pages_visited": pages_visited,
                    "rows": len(rows_by_url),
                    "sku_complete": sum(bool(clean_text(x.get("sku"))) for x in rows_by_url.values()),
                    "price_complete": sum(x.get("price_current") is not None for x in rows_by_url.values()),
                    "sample_cards": sample_cards,
                    "store_context": "online_catalog_no_store_requested",
                }
                (DIAGNOSTICS / f"farmacias_del_ahorro_{category.id}.json").write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                (DIAGNOSTICS / f"farmacias_del_ahorro_{category.id}.html").write_text(
                    page.content(), encoding="utf-8"
                )
                page.screenshot(
                    path=str(DIAGNOSTICS / f"farmacias_del_ahorro_{category.id}.png"),
                    full_page=True,
                )
                return list(rows_by_url.values())
            finally:
                context.close()
                browser.close()
