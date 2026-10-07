from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import (
    parse_qsl,
    unquote,
    urlencode,
    urljoin,
    urlsplit,
    urlunsplit,
)

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

    @staticmethod
    def _repair_mojibake(value: str | None) -> str:
        text = clean_text(value) or ""
        if not text or "Ã" not in text:
            return text
        try:
            repaired = text.encode("latin1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return text
        return clean_text(repaired) or text

    @staticmethod
    def _catalog_page_url(base_url: str, page_number: int) -> str:
        parts = urlsplit(base_url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["marca"] = "TODAS"
        query["n"] = "36"
        query["o"] = "3"
        if page_number <= 1:
            query.pop("p", None)
        else:
            query["p"] = str(page_number)
        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode(query),
                parts.fragment,
            )
        )

    @classmethod
    def _parse_pdp_detail(
        cls,
        response,
        fallback_title: str | None,
    ) -> dict:
        raw_body = " ".join(response.css("body ::text").getall())
        body = cls._repair_mojibake(raw_body)
        fallback = cls._repair_mojibake(fallback_title)

        detail = IbarraMayoreoScraper._parse_box_detail(
            body,
            fallback,
        )

        if not detail.get("sku"):
            match = re.search(
                r"\bSKU\s*:?\s*([A-Za-z0-9._-]+)",
                body,
                flags=re.IGNORECASE,
            )
            if match:
                detail["sku"] = clean_text(match.group(1))

        if not detail.get("brand"):
            match = re.search(
                r"\bMarca\s*:?\s*(.{2,60}?)"
                r"(?=\s+\d+\s+de\s+5|\s+Presentaci[oó]n\s*:)",
                body,
                flags=re.IGNORECASE,
            )
            if match:
                detail["brand"] = clean_text(match.group(1))

        return detail

    def __init__(
        self,
        category: str = "dentifricos-abarrotes",
        location: str = "ibarra-online",
        max_pages: int | str = 30,
        rows_per_page: int | str = 50,
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
        self.product_candidates: dict[str, dict] = {}
        self.parsed_product_urls: set[str] = set()
        self.yielded_product_urls: set[str] = set()
        self.failed_product_requests: list[dict] = []
        self.no_box_products: list[dict] = []
        self.catalog_orphan_keys: set[str] = set()
        self.catalog_orphan_rows: list[dict] = []
        self.catalog_pages: list[dict] = []
        self.parse_errors = 0
        self.no_box_available = 0

        self.output_path = str(
            Path("output")
            / "scrapy"
            / f"ibarra_mayoreo_{self.category.id}.xlsx"
        )

    async def start(self):
        yield scrapy.Request(
            self._catalog_page_url(self.category.url, 1),
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

            product_signal = bool(
                re.search(
                    r"Agregar al carrito|No disponible|Agotado|Sin existencia|"
                    r"art[ií]culo(?:\(s\)|s)?\s+por\s+"
                    r"(?:caja|bolsa|barra|garrafa|botella|paquete|saco)|"
                    r"\$\s*[0-9]",
                    card_text,
                    flags=re.IGNORECASE,
                )
            )
            ancestor_classes = " ".join(
                anchor.xpath(
                    "ancestor::*[position() <= 8]/@class"
                ).getall()
            )
            structural_product = bool(
                re.search(
                    r"product|producto|card|item|tile",
                    ancestor_classes,
                    flags=re.IGNORECASE,
                )
            )
            has_product_image = bool(
                anchor.css("img[alt]::attr(alt)").get()
                or anchor.css("img[title]::attr(title)").get()
            )

            if not product_signal and not (
                structural_product and has_product_image
            ):
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

    def _catalog_orphans(self, response) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()

        selectors = (
            '[class*="product"], [class*="Product"], '
            '[class*="card"], [class*="Card"], '
            '[class*="item"], [class*="Item"], '
            '[class*="tile"], [class*="Tile"]'
        )

        for container in response.css(selectors):
            text = self._repair_mojibake(
                " ".join(container.css("::text").getall())
            )
            if not text or len(text) < 8 or len(text) > 1400:
                continue
            if "CAJA" not in text.upper():
                continue
            if not re.search(r"\$\s*[0-9]", text):
                continue

            has_product_href = False
            for href in container.css("a[href]::attr(href)").getall():
                full_url = response.urljoin(href)
                try:
                    parsed = urlsplit(full_url)
                except Exception:
                    continue
                parts = [part for part in parsed.path.split("/") if part]
                if (
                    parsed.netloc.casefold()
                    == urlsplit(BASE_URL).netloc.casefold()
                    and len(parts) == 1
                ):
                    has_product_href = True
                    break

            if has_product_href:
                continue

            title = clean_text(
                " ".join(
                    container.css(
                        "h1::text, h2::text, h3::text, h4::text, "
                        "h5::text, [class*='name']::text, "
                        "[class*='title']::text"
                    ).getall()
                )
            )
            if not title:
                title = clean_text(
                    container.css("img::attr(alt)").get()
                    or container.css("img::attr(title)").get()
                )
            title = self._repair_mojibake(title)
            if not title or len(title) < 3 or len(title) > 220:
                continue

            detail = IbarraMayoreoScraper._parse_box_detail(
                text,
                title,
            )
            box_price = detail.get("box_price")
            if box_price is None:
                continue

            key = (
                (detail.get("sku") or "")
                + "|"
                + title.casefold()
                + "|"
                + str(box_price)
            )
            if key in seen:
                continue
            seen.add(key)

            out.append(
                {
                    "key": key,
                    "title": detail.get("product") or title,
                    "sku": detail.get("sku"),
                    "brand": detail.get("brand"),
                    "box_units": detail.get("box_units"),
                    "box_price": box_price,
                    "promotion": detail.get("promotion"),
                    "availability_status": detail.get(
                        "availability_status"
                    ),
                    "is_available": detail.get("is_available"),
                    "availability_raw": detail.get("availability_raw"),
                    "card_text": text,
                }
            )

        return out

    def _row_from_catalog_orphan(self, item: dict) -> dict:
        box_price = item.get("box_price")
        pack_count = item.get("box_units")
        if pack_count is not None:
            price_raw = (
                "CAJA | {} artículos por caja | ${:.2f} MXN".format(
                    pack_count,
                    box_price,
                )
            )
        else:
            price_raw = "CAJA | ${:.2f} MXN".format(box_price)

        now = datetime.now().astimezone().isoformat(timespec="seconds")

        return {
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
            "sku": item.get("sku"),
            "brand": item.get("brand"),
            "product": item.get("title"),
            "price_current": box_price,
            "price_regular": box_price,
            "promotion": item.get("promotion"),
            "availability_status": item.get("availability_status"),
            "is_available": item.get("is_available"),
            "availability_raw": item.get("availability_raw"),
            "pickup_available": None,
            "store_context_verified": False,
            "store_context_method": (
                "ibarra_scrapy_catalog_card_without_pdp"
            ),
            "url": None,
            "price_raw": price_raw,
        }

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
        orphans = self._catalog_orphans(response)
        new_links = 0
        new_orphans = 0
        seen_before = len(self.discovery_links)

        for orphan in orphans:
            key = orphan["key"]
            if key in self.catalog_orphan_keys:
                continue
            self.catalog_orphan_keys.add(key)
            self.catalog_orphan_rows.append(
                {
                    "page": page_number,
                    "title": orphan.get("title"),
                    "sku": orphan.get("sku"),
                    "price": orphan.get("box_price"),
                }
            )
            new_orphans += 1
            yield self._row_from_catalog_orphan(orphan)

        for item in links:
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
                cb_kwargs={"fallback_title": item.get("title")},
            )

        self.catalog_pages.append(
            {
                "page": page_number,
                "url": response.url,
                "links_on_page": len(links),
                "new_links": new_links,
                "duplicate_links": max(
                    len(links) - new_links,
                    0,
                ),
                "orphans_on_page": len(orphans),
                "new_orphans": new_orphans,
                "seen_before": seen_before,
                "cumulative_products": (
                    len(self.discovery_links)
                    + len(self.catalog_orphan_keys)
                ),
                "target": self.target_products,
                "last_page": self.last_page,
            }
        )

        self.logger.info(
            "IBARRA_CATALOG page=%s links=%s new=%s orphans=%s "
            "new_orphans=%s cumulative=%s target=%s last_page=%s",
            page_number,
            len(links),
            new_links,
            len(orphans),
            new_orphans,
            len(self.discovery_links) + len(self.catalog_orphan_keys),
            self.target_products,
            self.last_page,
        )

        if self.last_page is not None and page_number >= self.last_page:
            return
        if (
            self.last_page is None
            and self.target_products is not None
            and (
                len(self.discovery_links)
                + len(self.catalog_orphan_keys)
                >= self.target_products
            )
        ):
            return
        if page_number >= self.max_pages:
            return
        if not links and page_number > 1:
            return

        next_page = page_number + 1
        yield scrapy.Request(
            self._catalog_page_url(
                self.category.url,
                next_page,
            ),
            callback=self.parse_catalog,
            cb_kwargs={"page_number": next_page},
        )

    def errback_product(self, failure):
        request = failure.request
        info = {
            "url": request.url,
            "title": (
                (request.cb_kwargs or {}).get("fallback_title")
                if hasattr(request, "cb_kwargs")
                else None
            ),
            "error": str(failure.value),
        }
        self.failed_product_requests.append(info)
        self.logger.error(
            "IBARRA_PDP_FAILED url=%s error=%s",
            request.url,
            failure.value,
        )

    def parse_product(self, response, fallback_title: str | None = None):
        self.parsed_product_urls.add(response.url.rstrip("/"))
        if self._looks_blocked(response):
            raise CloseSpider("blocked")

        try:
            detail = self._parse_pdp_detail(
                response,
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
            self.no_box_products.append(
                {
                    "url": response.url,
                    "title": detail.get("product") or fallback_title,
                    "sku": detail.get("sku"),
                    "availability_status": detail.get(
                        "availability_status"
                    ),
                }
            )
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

        self.yielded_product_urls.add(response.url.rstrip("/"))

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
            "url": response.url,
            "price_raw": price_raw,
        }
