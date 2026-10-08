from __future__ import annotations

import base64
import json
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from ..availability import AVAILABLE, UNAVAILABLE, UNKNOWN
from ..config import Category, Location
from ..parsers import absolute_url, clean_text
from .chedraui import ChedrauiBlocked, ChedrauiStoreContextError
from .chedraui_polanco import ChedrauiScraper as PolancoUIScraper


class ChedrauiScraper(PolancoUIScraper):
    """Polanco scraper with VTEX productSearchV3 as the price authority.

    The storefront HTML remains useful for navigation and store-context
    verification, but price and promotion text can include category-level
    facets. We therefore capture the exact public productSearchV3 request made
    by the verified storefront and use its structured Price/ListPrice and
    product-level promotion fields whenever available. HTML rows are retained
    only as a fallback when the structured request is unavailable.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._product_search_template_url: str | None = None

    def _capture_product_search_request(self, request) -> None:
        url = request.url
        if "operationName=productSearchV3" in url:
            self._product_search_template_url = url

    def _collect_pages(self, page, category: Category, location: Location):
        page.on("request", self._capture_product_search_request)
        return super()._collect_pages(page, category, location)

    @staticmethod
    def _rewrite_product_search_range(url: str, start: int, end: int) -> str:
        parts = urlsplit(url)
        qs = parse_qs(parts.query, keep_blank_values=True)
        extensions = json.loads(qs["extensions"][0])
        encoded = extensions.get("variables")
        if not encoded:
            raise ValueError("productSearchV3 no contiene variables persistidas")
        variables = json.loads(base64.b64decode(encoded).decode("utf-8"))
        variables["from"] = start
        variables["to"] = end
        extensions["variables"] = base64.b64encode(
            json.dumps(variables, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        flat = {key: values[-1] for key, values in qs.items()}
        flat["extensions"] = json.dumps(extensions, separators=(",", ":"))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(flat), parts.fragment))

    @staticmethod
    def _best_offer(product: dict) -> tuple[float | None, float | None, int | None, list[str]]:
        offers: list[tuple[int, float | None, float | None, int | None, list[str]]] = []
        for item in product.get("items") or []:
            if not isinstance(item, dict):
                continue
            for seller in item.get("sellers") or []:
                if not isinstance(seller, dict):
                    continue
                offer = seller.get("commertialOffer") or {}
                if not isinstance(offer, dict):
                    continue
                price = offer.get("Price")
                regular = offer.get("ListPrice")
                available = offer.get("AvailableQuantity")
                priority = 0 if seller.get("sellerDefault") else 1
                promo_names: list[str] = []
                for value in offer.get("discountHighlights") or []:
                    if isinstance(value, dict) and value.get("name"):
                        promo_names.append(str(value["name"]))
                for teaser in offer.get("teasers") or []:
                    if isinstance(teaser, dict) and teaser.get("name"):
                        promo_names.append(str(teaser["name"]))
                offers.append((priority, price, regular, available, promo_names))

        usable = [
            value for value in offers
            if value[1] is not None
            and float(value[1]) > 0
            and (value[3] is None or int(value[3]) > 0)
        ]
        if not usable:
            usable = [
                value
                for value in offers
                if value[1] is not None and float(value[1]) > 0
            ]
        if usable:
            usable.sort(key=lambda value: value[0])
            _, price, regular, available, promo_names = usable[0]
            return (
                float(price) if price is not None else None,
                float(regular) if regular is not None else None,
                int(available) if available is not None else None,
                promo_names,
            )

        # Un SKU con inventario explícitamente 0 sigue siendo información
        # válida aunque el storefront ya no publique precio. Se conserva para
        # poder medir quiebres de stock.
        stock_only = [
            value
            for value in offers
            if value[3] is not None and int(value[3]) <= 0
        ]
        if stock_only:
            stock_only.sort(key=lambda value: value[0])
            _, price, regular, available, promo_names = stock_only[0]
            return (
                float(price) if price is not None else None,
                float(regular) if regular is not None else None,
                int(available),
                promo_names,
            )

        price_range = product.get("priceRange") or {}
        if not isinstance(price_range, dict):
            price_range = {}
        selling_range = price_range.get("sellingPrice") or {}
        list_range = price_range.get("listPrice") or {}
        if not isinstance(selling_range, dict):
            selling_range = {}
        if not isinstance(list_range, dict):
            list_range = {}
        selling = selling_range.get("lowPrice")
        regular = list_range.get("lowPrice")
        return (
            float(selling) if selling is not None else None,
            float(regular) if regular is not None else None,
            None,
            [],
        )

    def _rows_from_product_search_payload(
        self,
        payload: dict,
        category: Category,
        location: Location,
    ) -> tuple[list[dict], int | None]:
        search = (payload.get("data") or {}).get("productSearch") or {}
        products = search.get("products") or []
        records_filtered = search.get("recordsFiltered")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        rows: list[dict] = []

        for product in products:
            if not isinstance(product, dict):
                continue
            sku = clean_text(str(product.get("productId") or product.get("productReference") or ""))
            name = clean_text(product.get("productName"))
            url = absolute_url(product.get("link"), "https://www.chedraui.com.mx/")
            if not sku or not name or not url:
                continue

            current, regular, available, promo_names = self._best_offer(product)
            if current is None and available != 0:
                continue
            if current is not None:
                if regular is None or regular <= 0:
                    regular = current
                if current > regular:
                    current, regular = regular, current
                if current < regular and not promo_names:
                    promo_names.append("Precio promocional")

            if available is None:
                availability_status = UNKNOWN
                is_available = None
                availability_raw = "productSearchV3 AvailableQuantity=None"
            elif available > 0:
                availability_status = AVAILABLE
                is_available = True
                availability_raw = f"productSearchV3 AvailableQuantity={available}"
            else:
                availability_status = UNAVAILABLE
                is_available = False
                availability_raw = f"productSearchV3 AvailableQuantity={available}"

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
                    "sku": sku,
                    "brand": clean_text(product.get("brand")) or self._infer_brand(name),
                    "product": name,
                    "price_current": current,
                    "price_regular": regular,
                    "promotion": clean_text(" | ".join(dict.fromkeys(promo_names))),
                    "availability_status": availability_status,
                    "is_available": is_available,
                    "availability_raw": availability_raw,
                    "pickup_available": available is None or available > 0,
                    "store_context_verified": True,
                    "store_context_method": f"{self._active_store_context_method}+productSearchV3",
                    "url": url,
                    "price_raw": (
                        f"productSearchV3 Price={current} "
                        f"ListPrice={regular} AvailableQuantity={available}"
                    ),
                }
            )

        return rows, int(records_filtered) if records_filtered is not None else None

    def _recover_page_from_product_search(
        self,
        page,
        category: Category,
        location: Location,
        page_number: int,
        target_rows: int | None = None,
    ) -> tuple[list[dict], dict]:
        info: dict = {"mode": "productSearchV3", "rows": 0}
        template = self._product_search_template_url
        if not template:
            info["reason"] = "template_not_captured"
            return [], info

        start = (page_number - 1) * 20
        end = start + 19
        url = self._rewrite_product_search_range(template, start, end)

        attempts: list[dict] = []
        best_rows: list[dict] = []
        best_records_filtered: int | None = None
        last_reason = "no_structured_price_rows"

        for attempt in range(1, 5):
            detail: dict = {"attempt": attempt}
            try:
                response = page.context.request.get(
                    url,
                    timeout=120_000,
                    headers={
                        "cache-control": "no-cache",
                        "pragma": "no-cache",
                    },
                )
                detail["status"] = response.status
                info["status"] = response.status

                if response.status != 200:
                    last_reason = f"http_{response.status}"
                    detail["reason"] = last_reason
                else:
                    payload = response.json()
                    rows, records_filtered = self._rows_from_product_search_payload(
                        payload,
                        category,
                        location,
                    )
                    detail["rows"] = len(rows)
                    detail["records_filtered"] = records_filtered

                    if len(rows) > len(best_rows):
                        best_rows = rows
                        best_records_filtered = records_filtered

                    complete = bool(rows) and (
                        target_rows is None
                        or len(rows) >= target_rows
                    )
                    if complete:
                        detail["accepted"] = True
                        attempts.append(detail)
                        info.update(
                            {
                                "rows": len(rows),
                                "records_filtered": records_filtered,
                                "from": start,
                                "to": end,
                                "attempts": attempts,
                            }
                        )
                        return rows, info

                    if rows:
                        last_reason = "structured_page_below_target"
                    else:
                        last_reason = "no_structured_price_rows"
                    detail["reason"] = last_reason
            except Exception as exc:
                last_reason = f"api_error:{type(exc).__name__}"
                detail["reason"] = last_reason

            attempts.append(detail)
            if attempt < 4:
                try:
                    page.wait_for_timeout(min(500 * attempt, 1_500))
                except Exception:
                    pass

        info.update(
            {
                "rows": len(best_rows),
                "records_filtered": best_records_filtered,
                "from": start,
                "to": end,
                "attempts": attempts,
                "reason": last_reason,
            }
        )
        return [], info

    @staticmethod
    def _html_row_has_category_facet_contamination(row: dict) -> bool:
        promotion = clean_text(row.get("promotion")) or ""
        normalized = promotion.casefold()
        for source, target in (
            ("á", "a"),
            ("é", "e"),
            ("í", "i"),
            ("ó", "o"),
            ("ú", "u"),
            ("ü", "u"),
        ):
            normalized = normalized.replace(source, target)
        return "promocionsi (" in normalized or "promocion si (" in normalized

    def _load_page_rows(self, page, category, location, page_number: int, target_rows: int | None):
        url, html_rows, attempts = super()._load_page_rows(
            page, category, location, page_number, target_rows
        )

        # Always request the same range from VTEX productSearchV3 after the
        # storefront has loaded. This keeps the verified store/session context
        # while making structured Price/ListPrice the authoritative source.
        api_rows, api_info = self._recover_page_from_product_search(
            page,
            category,
            location,
            page_number,
            target_rows=target_rows,
        )
        attempts.append(api_info)

        if api_rows:
            self.run_meta.setdefault("api_price_pages", []).append(page_number)
            return url, api_rows, attempts

        # Once a structured productSearchV3 request has been captured, do not
        # silently fall back to HTML prices. The storefront HTML can contain
        # category-level facets such as "Promoción Sí (111)" alongside unrelated
        # price text, which caused false values such as 17 / 1299. Prefer an
        # incomplete but trustworthy page over corrupted price records.
        if self._product_search_template_url:
            self.run_meta.setdefault("structured_price_missing_pages", []).append(
                {
                    "page": page_number,
                    "html_rows_rejected": len(html_rows),
                    "api_reason": api_info.get("reason"),
                    "api_status": api_info.get("status"),
                    "api_rows": api_info.get("rows"),
                    "api_attempts": len(api_info.get("attempts") or []),
                }
            )
            return url, [], attempts

        # Legacy fallback only when no productSearchV3 template was captured at
        # all. Even then, reject the known category-facet contamination pattern.
        safe_html_rows = [
            row for row in html_rows
            if not self._html_row_has_category_facet_contamination(row)
        ]
        rejected = len(html_rows) - len(safe_html_rows)
        if rejected:
            self.run_meta.setdefault("html_facet_rows_rejected", []).append(
                {"page": page_number, "rows": rejected}
            )
        return url, safe_html_rows, attempts


__all__ = [
    "ChedrauiBlocked",
    "ChedrauiStoreContextError",
    "ChedrauiScraper",
]
