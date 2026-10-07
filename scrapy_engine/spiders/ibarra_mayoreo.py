from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit

import scrapy
from scrapy.exceptions import CloseSpider

from scraper.availability import UNAVAILABLE
from scraper.config import load_categories, load_locations
from scraper.parsers import clean_text
from scraper.retailers.ibarra_mayoreo import (
    BASE_URL,
    IbarraMayoreoScraper,
)


class IbarraMayoreoSpider(scrapy.Spider):
    name = "ibarra_mayoreo"
    retailer_label = "Ibarra Mayoreo"
    allowed_domains = ["ibarramayoreo.com"]

    custom_settings = {
        "CONCURRENT_REQUESTS_PER_DOMAIN": 4,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 2.0,
    }

    BLOCK_MARKERS = tuple(IbarraMayoreoScraper.BLOCK_MARKERS)

    def __init__(
        self,
        category: str = "dentifricos-abarrotes",
        location: str = "ibarra-online",
        max_pages: int | str = 30,
        update_consolidated: bool | str = False,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)

        categories = {
            item.id: item
            for item in load_categories(
                "config/ibarra-mayoreo/categories.yaml"
            )
        }
        locations = {
            item.id: item
            for item in load_locations("config/locations.yaml")
        }

        if category not in categories:
            raise ValueError(
                f"Categoria Ibarra Mayoreo no encontrada: {category}"
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
        self.last_page: int | None = None
        self.api_pages = 0
        self.discovery_links: set[str] = set()
        self.parse_errors = 0
        self.no_box_available = 0

        self.output_path = str(
            Path("output")
            / "scrapy"
            / f"ibarra_mayoreo_{self.category.id}.xlsx"
        )

    async def start(self):
        yield scrapy.Request(
            self.category.url,
            callback=self.parse_catalog,
            cb_kwargs={"page_number": 1},
        )

    @classmethod
    def _looks_blocked(cls, response) -> bool:
        if response.status in {401, 403, 429}:
            return True
        text = clean_text(" ".join(response.css("body ::text").getall())) or ""
        folded = text.casefold()
        return any(marker in folded for marker in cls.BLOCK_MARKERS)

    def _catalog_metadata(self, response) -> tuple[int | None, int | None]:
        target = None
        last_page = None
        target_path = (
            unquote(urlsplit(self.category.url).path)
            .rstrip("/")
            .casefold()
        )

        for anchor in response.css("a[href]"):
            href = anchor.attrib.get("href")
            if not href:
                continue

            full_url = response.urljoin(href)
            try:
                parsed = urlsplit(full_url)
                path = unquote(parsed.path).rstrip("/").casefold()
            except Exception:
                continue

            if path == target_path:
                label = clean_text(" ".join(anchor.css("::text").getall())) or ""
                match = re.search(r"\(([\d,]+)\)", label)
                if match:
                    try:
                        value = int(match.group(1).replace(",", ""))
                        target = max(target or 0, value)
                    except ValueError:
                        pass

            try:
                query = dict(parse_qsl(parsed.query, keep_blank_values=True))
                raw_page = query.get("p")
                if raw_page is not None:
                    value = int(raw_page)
                    if value > 0:
                        last_page = max(last_page or 0, value)
            except Exception:
                pass

        return target, last_page

    @staticmethod
    def _root_product_links(response) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()

        for anchor in response.css("a[href]"):
            href = anchor.attrib.get("href")
            if not href:
                continue

            full_url = response.urljoin(href)
            try:
                parsed = urlsplit(full_url)
            except Exception:
                continue

            if parsed.netloc.casefold() != urlsplit(BASE_URL).netloc.casefold():
                continue

            parts = [part for part in parsed.path.split("/") if part]
            lower = parsed.geturl().casefold()
            if len(parts) != 1:
                continue
            if any(
                marker in lower
                for marker in (
                    "/carrito",
                    "/cuenta",
                    "/favorit",
                    "/login",
                    "/contact",
                )
            ):
                continue

            card = anchor.xpath(
                "ancestor::*[self::article or self::li or self::div][1]"
            )
            card_text = clean_text(
                " ".join(card.css("::text").getall())
                if card
                else " ".join(anchor.css("::text").getall())
            ) or ""

            if not re.search(
                r"Agregar al carrito|No disponible|Agotado|Sin existencia|"
                r"art[ií]culo(?:\(s\)|s)?\s+por\s+"
                r"(?:caja|bolsa|barra|garrafa|botella|paquete|saco)|"
                r"\$\s*[0-9]",
                card_text,
                flags=re.IGNORECASE,
            ):
                continue

            title = (
                clean_text(anchor.attrib.get("title"))
                or clean_text(anchor.attrib.get("aria-label"))
                or clean_text(" ".join(anchor.css("::text").getall()))
            )

            if not title or len(title) < 3 or len(title) > 220:
                image_alt = anchor.css("img::attr(alt)").get()
                title = clean_text(image_alt)

            if not title or len(title) < 3 or len(title) > 220:
                continue

            key = (
                parsed.scheme
                + "://"
                + parsed.netloc
                + parsed.path.rstrip("/")
            )
            if key in seen:
                continue

            seen.add(key)
            out.append(
                {
                    "href": key,
                    "title": title,
                    "card_text": card_text,
                }
            )

        return out

    def parse_catalog(self, response, page_number: int):
        if self._looks_blocked(response):
            raise CloseSpider("blocked")

        self.api_pages += 1

        target, last_page = self._catalog_metadata(response)
        if target is not None:
            self.target_products = max(self.target_products or 0, target)
        if last_page is not None:
            self.last_page = max(self.last_page or 0, last_page)

        links = self._root_product_links(response)
        new_links = 0

        for item in links:
            href = item["href"]
            if href not in self.discovery_links:
                self.discovery_links.add(href)
                new_links += 1

            yield scrapy.Request(
                href,
                callback=self.parse_product,
                cb_kwargs={"fallback_title": item.get("title")},
            )

        self.logger.info(
            "IBARRA_CATALOG page=%s links=%s new=%s cumulative=%s "
            "target=%s last_page=%s",
            page_number,
            len(links),
            new_links,
            len(self.discovery_links),
            self.target_products,
            self.last_page,
        )

        if self.last_page is not None and page_number >= self.last_page:
            return
        if (
            self.last_page is None
            and self.target_products is not None
            and len(self.discovery_links) >= self.target_products
        ):
            return
        if page_number >= self.max_pages:
            return
        if not links and page_number > 1:
            return

        next_page = page_number + 1
        yield scrapy.Request(
            IbarraMayoreoScraper._page_url(
                self.category.url,
                next_page,
            ),
            callback=self.parse_catalog,
            cb_kwargs={"page_number": next_page},
        )

    def parse_product(self, response, fallback_title: str | None = None):
        if self._looks_blocked(response):
            raise CloseSpider("blocked")

        body = clean_text(" ".join(response.css("body ::text").getall())) or ""
        try:
            detail = IbarraMayoreoScraper._parse_box_detail(
                body,
                fallback_title,
            )
        except Exception:
            self.parse_errors += 1
            self.logger.exception(
                "No se pudo parsear detalle Ibarra: %s",
                response.url,
            )
            return

        if (
            detail.get("box_price") is None
            and detail.get("availability_status") != UNAVAILABLE
        ):
            self.no_box_available += 1
            self.logger.warning(
                "IBARRA_NO_BOX_PRICE sku=%s product=%s url=%s",
                detail.get("sku"),
                detail.get("product") or fallback_title,
                response.url,
            )
            return

        pack_count = detail.get("box_units")
        box_price = detail.get("box_price")
        if box_price is None:
            price_raw = detail.get("availability_raw") or "UNAVAILABLE"
        elif pack_count is not None:
            price_raw = (
                f"CAJA | {pack_count} artículos por caja | "
                f"${box_price:.2f} MXN"
            )
        else:
            price_raw = f"CAJA | ${box_price:.2f} MXN"

        now = datetime.now().astimezone().isoformat(timespec="seconds")

        yield {
            "scrape_timestamp": now,
            "retailer": "Ibarra Mayoreo",
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
            "brand": detail.get("brand"),
            "product": detail.get("product") or fallback_title,
            "price_current": box_price,
            "price_regular": box_price,
            "promotion": detail.get("promotion"),
            "availability_status": detail.get("availability_status"),
            "is_available": detail.get("is_available"),
            "availability_raw": detail.get("availability_raw"),
            "pickup_available": None,
            "store_context_verified": False,
            "store_context_method": "ibarra_scrapy_product_detail_box",
            "url": urljoin(BASE_URL, response.url),
            "price_raw": price_raw,
        }
