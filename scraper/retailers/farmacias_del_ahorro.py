from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from playwright.sync_api import sync_playwright

from ..config import Category, Location
from ..parsers import clean_text


BASE_URL = "https://www.fahorro.com/"
BROWSE_ENDPOINT = "https://api.empathy.co/search/v1/query/fda/browse"
DIAGNOSTICS = Path("diagnostics")


class FarmaciasDelAhorroBlocked(RuntimeError):
    pass


class FarmaciasDelAhorroNetworkUnavailable(RuntimeError):
    pass


class FarmaciasDelAhorroScraper:
    """Extractor del catálogo online público de Farmacias del Ahorro.

    El storefront usa Empathy Search para poblar las categorías. El scraper
    obtiene del HTML el categoryId vigente y consulta la misma API pública que
    utiliza el frontend, recorriendo start/rows hasta catalog.pagination.total.
    """

    MONEY_RE = re.compile(r"(?:MXN\s*)?\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)", re.IGNORECASE)
    SKU_PATTERNS = [
        re.compile(r'itemprop=["\']sku["\'][^>]*content=["\']([^"\']+)', re.IGNORECASE),
        re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+itemprop=["\']sku["\']', re.IGNORECASE),
        re.compile(r'itemprop=["\']sku["\'][^>]*>\s*([^<]+)', re.IGNORECASE),
        re.compile(r'"sku"\s*:\s*"([^"]+)"', re.IGNORECASE),
        re.compile(r'\bSKU\b\s*</[^>]+>\s*<[^>]+>\s*([^<]+)', re.IGNORECASE),
    ]
    BLOCK_MARKERS = (
        "access denied",
        "request rejected",
        "verify you are human",
        "verifica que eres humano",
        "captcha",
    )

    def __init__(self, headless: bool = True, max_pages: int = 100, rows_per_page: int = 50) -> None:
        self.headless = headless  # compatibilidad con el resto de retailers
        self.max_pages = max_pages
        self.rows_per_page = rows_per_page

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
        """Compatibilidad y pruebas de parsing para HTML de fichas/listados."""
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
        return current, regular, " | ".join(dict.fromkeys(promo)) if promo else None

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
    def _extract_category_id(html: str) -> str | None:
        # Prioriza la configuración del componente Empathy de la página.
        component = re.search(
            r'"component"\s*:\s*"Infinite_EmpathySearch/js/view/search-list-component"(.{0,12000}?)"categoryId"\s*:\s*"?(\d+)"?',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if component:
            return component.group(2)
        matches = re.findall(r'"categoryId"\s*:\s*"?(\d+)"?', html, flags=re.IGNORECASE)
        return matches[-1] if matches else None

    @staticmethod
    def _browse_url(category_id: str, start: int, rows: int) -> str:
        params = {
            "origin": "search_box",
            "start": str(start),
            "rows": str(rows),
            "lang": "es",
            "scope": "desktop",
            "channel": "tl",
            "region": "NACIONAL",
            "browseField": "categoryIds",
            "browseValue": category_id,
        }
        return BROWSE_ENDPOINT + "?" + urlencode(params)

    @staticmethod
    def _promotion(current: float | None, regular: float | None) -> str | None:
        if current is None or regular is None or regular <= 0 or current >= regular:
            return None
        discount = round((1 - current / regular) * 100)
        if discount > 0:
            return f"{discount}% de descuento | Precio promocional"
        return "Precio promocional"

    @staticmethod
    def _to_price(value) -> float | None:
        if value is None:
            return None
        try:
            return round(float(value), 2)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _row_from_item(cls, item: dict, category: Category, location: Location, now: str) -> dict | None:
        sku = clean_text(item.get("sku"))
        product = clean_text(item.get("ecommTitle"))
        url_key = clean_text(item.get("ecommUrlKey"))
        current = cls._to_price(item.get("currentPrice"))
        if not sku or not product or not url_key or current is None:
            return None
        regular = cls._to_price(item.get("previousPrice"))
        if regular is None or regular < current:
            regular = current
        brand = clean_text(item.get("ecommBrand")) or cls._infer_brand(product)
        product_url = urljoin(BASE_URL, f"{url_key}.html")
        return {
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
            "promotion": cls._promotion(current, regular),
            "pickup_available": None,
            "store_context_verified": False,
            "store_context_method": "online_catalog_empathy_nacional",
            "url": product_url,
            "price_raw": json.dumps(
                {"currentPrice": item.get("currentPrice"), "previousPrice": item.get("previousPrice")},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }

    def scrape_category(self, category: Category, location: Location) -> list[dict]:
        DIAGNOSTICS.mkdir(parents=True, exist_ok=True)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with sync_playwright() as p:
            request = p.request.new_context(
                extra_http_headers={
                    "Accept": "application/json,text/html,*/*;q=0.8",
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/139.0.0.0 Safari/537.36"
                    ),
                }
            )
            try:
                try:
                    page_response = request.get(category.url, timeout=45_000)
                except Exception as exc:
                    raise FarmaciasDelAhorroNetworkUnavailable(
                        f"No fue posible cargar la categoría {category.url}: {exc}"
                    ) from exc
                if not page_response.ok:
                    raise FarmaciasDelAhorroNetworkUnavailable(
                        f"HTTP {page_response.status} en {category.url}"
                    )
                html = page_response.text()
                folded = html.casefold()
                if any(marker in folded for marker in self.BLOCK_MARKERS):
                    raise FarmaciasDelAhorroBlocked(
                        "Farmacias del Ahorro presentó un bloqueo o verificación"
                    )
                numeric_category_id = self._extract_category_id(html)
                if not numeric_category_id:
                    raise RuntimeError(f"No se encontró categoryId de Empathy para {category.id}")

                rows_by_key: dict[tuple[str, str], dict] = {}
                total: int | None = None
                start = 0
                api_pages = 0
                for _ in range(self.max_pages):
                    api_url = self._browse_url(numeric_category_id, start, self.rows_per_page)
                    try:
                        response = request.get(api_url, timeout=45_000)
                    except Exception as exc:
                        raise FarmaciasDelAhorroNetworkUnavailable(
                            f"No fue posible consultar Empathy para {category.id}: {exc}"
                        ) from exc
                    if not response.ok:
                        raise FarmaciasDelAhorroNetworkUnavailable(
                            f"Empathy HTTP {response.status} para {category.id}"
                        )
                    data = response.json()
                    catalog = data.get("catalog") or {}
                    pagination = catalog.get("pagination") or {}
                    content = catalog.get("content") or []
                    api_pages += 1
                    if total is None:
                        total = int(pagination.get("total") or 0)
                    if not content:
                        break
                    for item in content:
                        row = self._row_from_item(item, category, location, now)
                        if row is not None:
                            rows_by_key[(row["sku"], row["url"])] = row
                    start += len(content)
                    if total is not None and start >= total:
                        break

                rows = list(rows_by_key.values())
                meta = {
                    "category_id": category.id,
                    "empathy_category_id": numeric_category_id,
                    "url": category.url,
                    "target_products": total,
                    "api_pages": api_pages,
                    "rows": len(rows),
                    "sku_complete": sum(bool(clean_text(x.get("sku"))) for x in rows),
                    "price_complete": sum(x.get("price_current") is not None for x in rows),
                    "regular_price_complete": sum(x.get("price_regular") is not None for x in rows),
                    "url_complete": sum(bool(clean_text(x.get("url"))) for x in rows),
                    "store_context": "online_catalog_empathy_nacional",
                }
                (DIAGNOSTICS / f"farmacias_del_ahorro_{category.id}.json").write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                return rows
            finally:
                request.dispose()
