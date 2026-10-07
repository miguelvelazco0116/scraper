from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import scrapy
from scrapy.exceptions import CloseSpider

from scraper.availability import UNAVAILABLE, availability_fields
from scraper.config import load_categories, load_locations
from scraper.parsers import clean_text
from scraper.retailers.farmacias_similares import (
    BASE_URL,
    FarmaciasSimilaresScraper,
)


class FarmaciasSimilaresSpider(scrapy.Spider):
    name = "farmacias_similares"
    retailer_label = "Farmacias Similares"
    allowed_domains = ["farmaciasdesimilares.com"]

    custom_settings = {
        "CONCURRENT_REQUESTS_PER_DOMAIN": 3,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 1.5,
    }

    BLOCK_MARKERS = tuple(FarmaciasSimilaresScraper.BLOCK_MARKERS)

    def __init__(
        self,
        category: str = "condones",
        location: str = "similares-online",
        max_pages: int | str = 20,
        rows_per_page: int | str = 50,
        update_consolidated: bool | str = False,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)

        categories = {
            item.id: item
            for item in load_categories(
                "config/farmacias-similares/categories.yaml"
            )
        }
        locations = {
            item.id: item
            for item in load_locations("config/locations.yaml")
        }

        if category not in categories:
            raise ValueError(
                f"Categoria Farmacias Similares no encontrada: {category}"
            )
        if location not in locations:
            raise ValueError(f"Ubicacion no encontrada: {location}")

        self.category = categories[category]
        self.location = locations[location]
        self.category_id = self.category.id
        self.location_id = self.location.id
        self.max_pages = int(max_pages)
        self.update_consolidated = update_consolidated

        self.target_products: int | None = None
        self.api_pages = 0
        self.discovery_links: set[str] = set()
        self.product_candidates: dict[str, dict] = {}
        self.parsed_product_urls: set[str] = set()
        self.yielded_product_urls: set[str] = set()
        self.failed_product_requests: list[dict] = []
        self.missing_price_products: list[dict] = []
        self.catalog_orphan_keys: set[str] = set()
        self.catalog_orphan_rows: list[dict] = []
        self.catalog_pages: list[dict] = []
        self.parse_errors = 0
        self._stale_pages = 0

        self.output_path = str(
            Path("output")
            / "scrapy"
            / f"farmacias_similares_{self.category.id}.xlsx"
        )

    async def start(self):
        yield scrapy.Request(
            self.category.url,
            callback=self.parse_catalog,
            cb_kwargs={"page_number": 1},
            headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
        )

    @staticmethod
    def _body_text(response) -> str:
        return "\n".join(
            text
            for text in response.css("body ::text").getall()
            if clean_text(text)
        )

    @classmethod
    def _looks_blocked(cls, response) -> bool:
        if response.status in {401, 403, 429}:
            return True
        if "/blocked" in (response.url or "").casefold():
            return True
        body = FarmaciasSimilaresScraper._normalize(
            cls._body_text(response)
        )
        return any(
            FarmaciasSimilaresScraper._normalize(marker) in body
            for marker in cls.BLOCK_MARKERS
        )

    @classmethod
    def _target_count(cls, response) -> int | None:
        values: list[int] = []
        body = cls._body_text(response)
        for match in FarmaciasSimilaresScraper.TARGET_RE.finditer(body):
            try:
                values.append(int(match.group(1)))
            except ValueError:
                continue
        return max(values) if values else None

    @staticmethod
    def _same_origin_product_url(response, href: str | None) -> str | None:
        if not href:
            return None
        full_url = response.urljoin(href)
        try:
            parsed = urlsplit(full_url)
            base = urlsplit(BASE_URL)
        except Exception:
            return None
        if parsed.netloc.casefold() != base.netloc.casefold():
            return None
        if not re.search(r"/p/?$", parsed.path, flags=re.IGNORECASE):
            return None
        return (
            parsed.scheme
            + "://"
            + parsed.netloc
            + parsed.path.rstrip("/")
        )

    @staticmethod
    def _selector_text(selector) -> str:
        return clean_text(" ".join(selector.css("::text").getall())) or ""

    @classmethod
    def _nearest_product_card(cls, anchor):
        ancestors = list(
            anchor.xpath(
                "ancestor::*[self::article or self::li or self::div]"
            )
        )
        product_signal = re.compile(
            r"Comprar ahora|Agotado|No disponible|Sin existencia|"
            r"Sin stock|Temporalmente no disponible|\$\s*[0-9]",
            flags=re.IGNORECASE,
        )
        for node in reversed(ancestors[-10:]):
            text = cls._selector_text(node)
            if 10 <= len(text) <= 2800 and product_signal.search(text):
                return node
        return None

    @classmethod
    def _title_from(cls, anchor, card) -> str | None:
        candidates = [
            anchor.attrib.get("aria-label"),
            anchor.attrib.get("title"),
            " ".join(anchor.css("::text").getall()),
        ]
        if card is not None:
            candidates.extend(
                [
                    " ".join(
                        card.css(
                            "h1::text,h2::text,h3::text,h4::text,h5::text"
                        ).getall()
                    ),
                    card.css("img::attr(alt)").get(),
                ]
            )
        candidates.append(anchor.css("img::attr(alt)").get())

        for value in candidates:
            title = clean_text(value)
            if title and 3 <= len(title) <= 260:
                return title
        return None

    @classmethod
    def _card_price_fields(cls, card_text: str) -> dict:
        values = [
            FarmaciasSimilaresScraper._float(raw)
            for raw in FarmaciasSimilaresScraper.MONEY_RE.findall(
                card_text or ""
            )
        ]
        values = [
            value
            for value in values
            if value is not None and value > 0
        ]

        current = None
        regular = None
        if len(values) >= 2:
            regular = values[0]
            current = values[-1]
        elif len(values) == 1:
            current = values[0]
            regular = current

        promotion = None
        discount = re.search(
            r"(-\s*\d+(?:\.\d+)?\s*%)",
            card_text or "",
            flags=re.IGNORECASE,
        )
        if discount:
            promotion = clean_text(discount.group(1))
        elif (
            current is not None
            and regular is not None
            and current < regular
        ):
            promotion = "Precio promocional"

        return {
            "price_current": current,
            "price_regular": regular,
            "promotion": promotion,
        }

    @classmethod
    def _catalog_products(cls, response) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()

        for anchor in response.css("a[href]"):
            href = cls._same_origin_product_url(
                response,
                anchor.attrib.get("href"),
            )
            if not href or href in seen:
                continue

            card = cls._nearest_product_card(anchor)
            if card is None:
                continue
            card_text = cls._selector_text(card)
            title = cls._title_from(anchor, card)
            if not title:
                continue

            price = cls._card_price_fields(card_text)
            availability = availability_fields(text=card_text)

            seen.add(href)
            out.append(
                {
                    "href": href,
                    "title": title,
                    "card_text": card_text,
                    **price,
                    **availability,
                    "source": "pdp_link",
                }
            )

        return out

    @classmethod
    def _unavailable_cards_without_pdp(cls, response) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        selector = (
            "article, li, [class*='product'], [class*='Product'], "
            "[class*='card'], [class*='Card'], "
            "[class*='item'], [class*='Item']"
        )

        candidates = list(response.css(selector))
        for node in candidates:
            text = cls._selector_text(node)
            availability = availability_fields(text=text)
            if availability["availability_status"] != UNAVAILABLE:
                continue
            if not text or len(text) < 10 or len(text) > 2800:
                continue

            has_pdp = any(
                cls._same_origin_product_url(response, href)
                for href in node.css("a[href]::attr(href)").getall()
            )
            if has_pdp:
                continue

            nested_unavailable = False
            for child in node.css(selector):
                if child.root is node.root:
                    continue
                child_text = cls._selector_text(child)
                child_availability = availability_fields(text=child_text)
                if child_availability["availability_status"] == UNAVAILABLE:
                    nested_unavailable = True
                    break
            if nested_unavailable:
                continue

            title = clean_text(
                " ".join(
                    node.css(
                        "h1::text,h2::text,h3::text,h4::text,h5::text,"
                        "[class*='title']::text,[class*='name']::text"
                    ).getall()
                )
            )
            if not title:
                title = clean_text(node.css("img::attr(alt)").get())
            if not title or len(title) > 260:
                continue

            key = title.casefold()
            if key in seen:
                continue
            seen.add(key)

            out.append(
                {
                    "key": f"unavailable::{key}",
                    "title": title,
                    "card_text": text,
                    **cls._card_price_fields(text),
                    **availability,
                    "source": "unavailable_card",
                }
            )

        return out

    @staticmethod
    def _infer_brand(title: str | None) -> str | None:
        blob = title or ""
        for candidate in FarmaciasSimilaresScraper.BRAND_CANDIDATES:
            if re.search(
                rf"(?<!\w){re.escape(candidate)}(?!\w)",
                blob,
                re.IGNORECASE,
            ):
                return candidate
        return None

    def _unavailable_row(self, item: dict) -> dict:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        return {
            "scrape_timestamp": now,
            "retailer": "Farmacias Similares",
            "city": self.location.city,
            "state": self.location.state,
            "postal_code": self.location.postal_code,
            "store": self.location.store,
            "store_id": self.location.store_id,
            "department": self.category.department,
            "category": self.category.name,
            "subcategory": self.category.subcategory,
            "sub_subcategory": self.category.sub_subcategory,
            "category_id": self.category.id,
            "sku": None,
            "brand": self._infer_brand(item.get("title")),
            "product": item.get("title"),
            "price_current": item.get("price_current"),
            "price_regular": item.get("price_regular"),
            "promotion": item.get("promotion"),
            "availability_status": item.get("availability_status"),
            "is_available": item.get("is_available"),
            "availability_raw": item.get("availability_raw"),
            "pickup_available": None,
            "store_context_verified": False,
            "store_context_method": (
                "similares_scrapy_category_unavailable_card"
            ),
            "url": None,
            "price_raw": item.get("card_text"),
        }

    def parse_catalog(self, response, page_number: int):
        if self._looks_blocked(response):
            raise CloseSpider("blocked")

        self.api_pages += 1
        target = self._target_count(response)
        if target is not None:
            self.target_products = max(self.target_products or 0, target)

        products = self._catalog_products(response)
        unavailable_cards = self._unavailable_cards_without_pdp(response)
        before = len(self.discovery_links) + len(self.catalog_orphan_keys)
        new_links = 0
        new_orphans = 0

        for item in unavailable_cards:
            key = item["key"]
            if key in self.catalog_orphan_keys:
                continue
            self.catalog_orphan_keys.add(key)
            self.catalog_orphan_rows.append(
                {
                    "page": page_number,
                    "title": item.get("title"),
                    "availability_status": item.get(
                        "availability_status"
                    ),
                }
            )
            new_orphans += 1
            yield self._unavailable_row(item)

        for item in products:
            href = item["href"]
            if href in self.discovery_links:
                continue
            self.discovery_links.add(href)
            self.product_candidates[href] = {
                "url": href,
                "title": item.get("title"),
                "catalog_page": page_number,
            }
            new_links += 1
            yield scrapy.Request(
                href,
                callback=self.parse_product,
                errback=self.errback_product,
                cb_kwargs={"catalog_item": item},
                headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
            )

        cumulative = (
            len(self.discovery_links) + len(self.catalog_orphan_keys)
        )
        self.catalog_pages.append(
            {
                "page": page_number,
                "url": response.url,
                "links_on_page": len(products),
                "new_links": new_links,
                "orphans_on_page": len(unavailable_cards),
                "new_orphans": new_orphans,
                "cumulative_products": cumulative,
                "target": self.target_products,
            }
        )

        self.logger.info(
            "SIMILARES_CATALOG page=%s links=%s new=%s orphans=%s "
            "new_orphans=%s cumulative=%s target=%s",
            page_number,
            len(products),
            new_links,
            len(unavailable_cards),
            new_orphans,
            cumulative,
            self.target_products,
        )

        if self.target_products is not None and cumulative >= self.target_products:
            return
        if page_number >= self.max_pages:
            return

        self._stale_pages = (
            self._stale_pages + 1
            if cumulative == before
            else 0
        )
        if self._stale_pages >= 2:
            return

        next_page = page_number + 1
        yield scrapy.Request(
            FarmaciasSimilaresScraper._page_url(
                self.category.url,
                next_page,
            ),
            callback=self.parse_catalog,
            cb_kwargs={"page_number": next_page},
            headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
        )

    def errback_product(self, failure):
        request = failure.request
        catalog_item = (
            (request.cb_kwargs or {}).get("catalog_item")
            if hasattr(request, "cb_kwargs")
            else {}
        ) or {}
        info = {
            "url": request.url,
            "title": catalog_item.get("title"),
            "error": str(failure.value),
        }
        self.failed_product_requests.append(info)
        self.logger.error(
            "SIMILARES_PDP_FAILED url=%s error=%s",
            request.url,
            failure.value,
        )

    def parse_product(self, response, catalog_item: dict):
        self.parsed_product_urls.add(response.url.rstrip("/"))
        if self._looks_blocked(response):
            raise CloseSpider("blocked")

        fallback_title = clean_text(catalog_item.get("title"))
        body = self._body_text(response)
        try:
            detail = FarmaciasSimilaresScraper._parse_detail(
                body,
                fallback_title,
            )
        except Exception:
            self.parse_errors += 1
            self.logger.exception(
                "No se pudo parsear PDP Similares: %s",
                response.url,
            )
            return

        catalog_status = catalog_item.get("availability_status")
        if (
            detail.get("availability_status") == "UNKNOWN"
            and catalog_status not in (None, "UNKNOWN")
        ):
            detail["availability_status"] = catalog_status
            detail["is_available"] = catalog_item.get("is_available")
            detail["availability_raw"] = catalog_item.get(
                "availability_raw"
            )

        current = catalog_item.get("price_current")
        regular = catalog_item.get("price_regular")
        promotion = catalog_item.get("promotion")

        if current is None:
            current = detail.get("price_current")
        if regular is None:
            regular = detail.get("price_regular")
        if regular is None:
            regular = current
        if not promotion:
            promotion = detail.get("promotion")

        if (
            current is None
            and detail.get("availability_status") != UNAVAILABLE
        ):
            self.missing_price_products.append(
                {
                    "url": response.url,
                    "sku": detail.get("sku"),
                    "title": detail.get("product") or fallback_title,
                }
            )
            self.logger.warning(
                "SIMILARES_NO_PRICE sku=%s product=%s url=%s",
                detail.get("sku"),
                detail.get("product") or fallback_title,
                response.url,
            )
            return

        raw = {
            "catalog_current": catalog_item.get("price_current"),
            "catalog_regular": catalog_item.get("price_regular"),
            "catalog_promotion": catalog_item.get("promotion"),
            "detail_current": detail.get("price_current"),
            "detail_regular": detail.get("price_regular"),
            "detail_promotion": detail.get("promotion"),
            "availability_status": detail.get("availability_status"),
            "availability_raw": detail.get("availability_raw"),
        }

        now = datetime.now().astimezone().isoformat(timespec="seconds")
        self.yielded_product_urls.add(response.url.rstrip("/"))

        yield {
            "scrape_timestamp": now,
            "retailer": "Farmacias Similares",
            "city": self.location.city,
            "state": self.location.state,
            "postal_code": self.location.postal_code,
            "store": self.location.store,
            "store_id": self.location.store_id,
            "department": self.category.department,
            "category": self.category.name,
            "subcategory": self.category.subcategory,
            "sub_subcategory": self.category.sub_subcategory,
            "category_id": self.category.id,
            "sku": detail.get("sku"),
            "brand": detail.get("brand")
            or self._infer_brand(detail.get("product") or fallback_title),
            "product": detail.get("product") or fallback_title,
            "price_current": current,
            "price_regular": regular,
            "promotion": promotion,
            "availability_status": detail.get("availability_status"),
            "is_available": detail.get("is_available"),
            "availability_raw": detail.get("availability_raw"),
            "pickup_available": None,
            "store_context_verified": False,
            "store_context_method": (
                "similares_scrapy_catalog_price_pdp_sku"
            ),
            "url": response.url,
            "price_raw": json.dumps(
                raw,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }
