from __future__ import annotations

from datetime import datetime
from pathlib import Path

import scrapy
from scrapy.exceptions import CloseSpider

from scraper.config import load_categories, load_locations
from scraper.retailers.farmacias_del_ahorro import FarmaciasDelAhorroScraper


class FarmaciasDelAhorroSpider(scrapy.Spider):
    name = "farmacias_del_ahorro"
    retailer_label = "Farmacias del Ahorro"
    allowed_domains = ["fahorro.com", "api.empathy.co"]

    custom_settings = {
        "CONCURRENT_REQUESTS_PER_DOMAIN": 4,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 2.0,
    }

    def __init__(
        self,
        category: str = "congestion-nasal",
        location: str = "fahorro-online",
        max_pages: int | str = 100,
        rows_per_page: int | str = 50,
        update_consolidated: bool | str = False,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)

        categories = {
            item.id: item
            for item in load_categories(
                "config/farmacias-del-ahorro/categories.yaml"
            )
        }
        locations = {
            item.id: item
            for item in load_locations("config/locations.yaml")
        }

        if category not in categories:
            raise ValueError(
                f"Categoria Farmacias del Ahorro no encontrada: {category}"
            )
        if location not in locations:
            raise ValueError(f"Ubicacion no encontrada: {location}")

        self.category = categories[category]
        self.location = locations[location]
        self.category_id = self.category.id
        self.location_id = self.location.id
        self.max_pages = int(max_pages)
        self.rows_per_page = int(rows_per_page)
        self.update_consolidated = update_consolidated

        self.target_products: int | None = None
        self.api_pages = 0
        self.numeric_category_id: str | None = None

        self.output_path = str(
            Path("output")
            / "scrapy"
            / f"farmacias_del_ahorro_{self.category.id}.xlsx"
        )

    async def start(self):
        yield scrapy.Request(
            self.category.url,
            callback=self.parse_category,
            headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
        )

    def parse_category(self, response):
        html = response.text
        if FarmaciasDelAhorroScraper._looks_blocked(html):
            raise CloseSpider("blocked")

        numeric_category_id = (
            FarmaciasDelAhorroScraper._extract_category_id(html)
        )
        if not numeric_category_id:
            raise CloseSpider("category_id_not_found")

        self.numeric_category_id = numeric_category_id
        api_url = FarmaciasDelAhorroScraper._browse_url(
            numeric_category_id,
            0,
            self.rows_per_page,
        )

        yield scrapy.Request(
            api_url,
            callback=self.parse_api,
            headers={"Accept": "application/json"},
            meta={"start": 0, "page_index": 0},
        )

    def parse_api(self, response):
        try:
            payload = response.json()
        except Exception as exc:
            raise CloseSpider(f"invalid_json:{exc}") from exc

        catalog = payload.get("catalog") or {}
        pagination = catalog.get("pagination") or {}
        content = catalog.get("content") or []

        self.api_pages += 1

        if self.target_products is None:
            raw_total = pagination.get("total")
            if raw_total is not None:
                self.target_products = int(raw_total)

        now = datetime.now().astimezone().isoformat(timespec="seconds")
        for item in content:
            row = FarmaciasDelAhorroScraper._row_from_item(
                item,
                self.category,
                self.location,
                now,
            )
            if row is not None:
                yield row

        if not content:
            return

        start = int(response.meta.get("start") or 0)
        page_index = int(response.meta.get("page_index") or 0)
        next_start = start + len(content)
        next_page_index = page_index + 1

        if next_page_index >= self.max_pages:
            return
        if (
            self.target_products is not None
            and next_start >= self.target_products
        ):
            return

        api_url = FarmaciasDelAhorroScraper._browse_url(
            self.numeric_category_id or "",
            next_start,
            self.rows_per_page,
        )
        yield scrapy.Request(
            api_url,
            callback=self.parse_api,
            headers={"Accept": "application/json"},
            meta={
                "start": next_start,
                "page_index": next_page_index,
            },
        )
