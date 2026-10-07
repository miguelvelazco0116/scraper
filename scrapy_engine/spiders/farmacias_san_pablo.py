from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import scrapy
from scrapy.exceptions import CloseSpider

from scraper.config import load_categories, load_locations
from scraper.parsers import clean_text
from scraper.retailers.farmacias_san_pablo import (
    FarmaciasSanPabloScraper,
)


API_BASE = (
    "https://api.farmaciasanpablo.com.mx/"
    "rest/v2/fsp/products/search-sponsored"
)


class FarmaciasSanPabloSpider(scrapy.Spider):
    name = "farmacias_san_pablo"
    retailer_label = "Farmacias San Pablo"
    allowed_domains = [
        "api.farmaciasanpablo.com.mx",
        "farmaciasanpablo.com.mx",
    ]
    discovery_source = "occ_search_sponsored"

    custom_settings = {
        "CONCURRENT_REQUESTS_PER_DOMAIN": 3,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 1.5,
    }

    def __init__(
        self,
        category: str = "enjuagues-bucales",
        location: str = "san-pablo-online",
        max_pages: int | str = 100,
        rows_per_page: int | str = 48,
        update_consolidated: bool | str = False,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)

        categories = {
            item.id: item
            for item in load_categories(
                "config/farmacias-san-pablo/categories.yaml"
            )
        }
        locations = {
            item.id: item
            for item in load_locations("config/locations.yaml")
        }

        if category not in categories:
            raise ValueError(
                f"Categoria Farmacias San Pablo no encontrada: {category}"
            )
        if location not in locations:
            raise ValueError(f"Ubicacion no encontrada: {location}")

        self.category = categories[category]
        self.location = locations[location]
        self.category_id = self.category.id
        self.location_id = self.location.id
        self.max_pages = int(max_pages)
        self.rows_per_page = min(48, max(1, int(rows_per_page)))
        self.update_consolidated = update_consolidated

        self.category_code = self._category_code(self.category.url)
        if not self.category_code:
            raise ValueError(
                "No se pudo obtener category code de San Pablo: "
                f"{self.category.url}"
            )

        self.target_products: int | None = None
        self.total_pages: int | None = None
        self.api_pages = 0
        self.discovered_products = 0
        self.api_product_keys: set[str] = set()
        self.failed_product_requests: list[dict] = []
        self.missing_price_products: list[dict] = []
        self.parse_errors = 0
        self.catalog_pages: list[dict] = []

        # Atributos comunes de diagnostico del pipeline.
        self.discovery_links: set[str] = set()
        self.product_candidates: dict[str, dict] = {}
        self.parsed_product_urls: set[str] = set()
        self.yielded_product_urls: set[str] = set()
        self.catalog_orphan_rows: list[dict] = []

        self.output_path = str(
            Path("output")
            / "scrapy"
            / f"farmacias_san_pablo_{self.category.id}.xlsx"
        )

    @staticmethod
    def _category_code(url: str) -> str | None:
        matches = re.findall(
            r"/c/(\d+)(?:/|$)",
            url or "",
            flags=re.IGNORECASE,
        )
        return matches[-1] if matches else None

    @staticmethod
    def _api_url(
        category_code: str,
        page_index: int,
        page_size: int,
    ) -> str:
        query = urlencode(
            {
                "pageSize": page_size,
                "currentPage": page_index,
                "query": (
                    f":relevance:allCategories:{category_code}"
                ),
            }
        )
        return f"{API_BASE}?{query}"

    def _request(self, page_index: int):
        return scrapy.Request(
            self._api_url(
                self.category_code,
                page_index,
                self.rows_per_page,
            ),
            callback=self.parse_api,
            cb_kwargs={"page_index": page_index},
            headers={
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://www.farmaciasanpablo.com.mx",
                "Referer": self.category.url,
            },
        )

    async def start(self):
        yield self._request(0)

    @staticmethod
    def _looks_blocked(response) -> bool:
        if response.status in {401, 403, 429}:
            return True
        body = (response.text or "").casefold()
        return any(
            marker in body
            for marker in (
                "access denied",
                "request rejected",
                "verify you are human",
                "verifica que eres humano",
                "captcha",
            )
        )

    def parse_api(self, response, page_index: int):
        if self._looks_blocked(response):
            raise CloseSpider("blocked")

        try:
            payload = response.json()
        except Exception as exc:
            raise CloseSpider(f"invalid_json:{exc}") from exc

        if not isinstance(payload, dict):
            raise CloseSpider("invalid_payload")

        self.api_pages += 1

        pagination = payload.get("pagination")
        if not isinstance(pagination, dict):
            pagination = {}

        raw_target = pagination.get("totalResults")
        if raw_target is not None:
            try:
                self.target_products = int(raw_target)
            except (TypeError, ValueError):
                pass

        raw_total_pages = pagination.get("totalPages")
        if raw_total_pages is not None:
            try:
                self.total_pages = int(raw_total_pages)
            except (TypeError, ValueError):
                pass

        page_products = payload.get("products")
        if not isinstance(page_products, list):
            page_products = []

        before = len(self.api_product_keys)
        now = datetime.now().astimezone().isoformat(timespec="seconds")

        for product in page_products:
            if not isinstance(product, dict):
                self.parse_errors += 1
                continue

            code = clean_text(str(product.get("code") or ""))
            href = clean_text(str(product.get("url") or ""))
            name = clean_text(str(product.get("name") or ""))
            key = code or href or name
            if not key:
                self.parse_errors += 1
                continue

            if key in self.api_product_keys:
                continue
            self.api_product_keys.add(key)

            row = FarmaciasSanPabloScraper._row_from_occ_product(
                product,
                self.category,
                self.location,
                now,
            )
            if row is None:
                self.parse_errors += 1
                continue

            if (
                row.get("price_current") is None
                and row.get("availability_status") != "UNAVAILABLE"
            ):
                self.missing_price_products.append(
                    {
                        "sku": row.get("sku"),
                        "product": row.get("product"),
                        "url": row.get("url"),
                    }
                )

            yield row

        self.discovered_products = len(self.api_product_keys)
        new_products = self.discovered_products - before
        self.catalog_pages.append(
            {
                "page": page_index + 1,
                "currentPage": page_index,
                "products_on_page": len(page_products),
                "new_products": new_products,
                "cumulative_products": self.discovered_products,
                "target": self.target_products,
                "total_pages": self.total_pages,
                "api_url": response.url,
            }
        )

        self.logger.info(
            "SAN_PABLO_OCC page=%s products=%s new=%s cumulative=%s "
            "target=%s total_pages=%s",
            page_index + 1,
            len(page_products),
            new_products,
            self.discovered_products,
            self.target_products,
            self.total_pages,
        )

        if not page_products:
            return
        if (
            self.target_products is not None
            and self.discovered_products >= self.target_products
        ):
            return

        next_page = page_index + 1
        if next_page >= self.max_pages:
            return
        if (
            self.total_pages is not None
            and next_page >= self.total_pages
        ):
            return

        yield self._request(next_page)
