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
        self._product_search_template_score: tuple[int, int, int, int] = (
            -1,
            -1,
            -1,
            -1,
        )
        self._product_search_category: Category | None = None
        self._product_search_candidates: list[dict] = []

    @staticmethod
    def _product_search_variables(url: str) -> dict:
        try:
            parts = urlsplit(url)
            qs = parse_qs(parts.query, keep_blank_values=True)
            extensions = json.loads(qs["extensions"][0])
            encoded = extensions.get("variables")
            if not encoded:
                return {}
            payload = base64.b64decode(encoded).decode("utf-8")
            variables = json.loads(payload)
            return variables if isinstance(variables, dict) else {}
        except Exception:
            return {}

    @classmethod
    def _category_affinity(
        cls,
        url: str,
        category: Category | None,
    ) -> int:
        if category is None:
            return 0

        variables = cls._product_search_variables(url)
        if not variables:
            return 0

        blob = json.dumps(
            variables,
            ensure_ascii=False,
            separators=(",", ":"),
        ).casefold()

        def normalized(value: str) -> str:
            return "".join(
                character
                for character in value.casefold()
                if character.isalnum()
            )

        normalized_blob = normalized(blob)
        path_segments = [
            value
            for value in urlsplit(category.url).path.split("/")
            if value
        ]
        normalized_segments = [
            normalized(value)
            for value in path_segments
            if normalized(value)
        ]
        if not normalized_segments:
            return 0

        matches = sum(
            segment in normalized_blob
            for segment in normalized_segments
        )
        # The leaf category is the strongest signal. Parent category names
        # can legitimately appear in unrelated storefront searches.
        leaf_match = normalized_segments[-1] in normalized_blob
        return matches + (10 if leaf_match else 0)

    @classmethod
    def _product_search_span(cls, url: str) -> int:
        variables = cls._product_search_variables(url)
        try:
            start = int(variables.get("from"))
            end = int(variables.get("to"))
        except (TypeError, ValueError):
            return 0
        return max(end - start + 1, 0)

    def _consider_product_search_template(
        self,
        url: str,
        *,
        records_filtered: int | None = None,
        product_count: int | None = None,
        source: str,
    ) -> None:
        if "operationName=productSearchV3" not in url:
            return

        affinity = self._category_affinity(
            url,
            self._product_search_category,
        )
        records_score = (
            int(records_filtered)
            if records_filtered is not None
            else -1
        )
        products_score = (
            int(product_count)
            if product_count is not None
            else -1
        )
        span = self._product_search_span(url)
        score = (
            affinity,
            records_score,
            products_score,
            span,
        )

        self._product_search_candidates.append(
            {
                "url": url,
                "source": source,
                "affinity": affinity,
                "records_filtered": records_filtered,
                "product_count": product_count,
                "span": span,
                "score": list(score),
            }
        )
        self._product_search_candidates = (
            self._product_search_candidates[-50:]
        )

        if score > self._product_search_template_score:
            self._product_search_template_score = score
            self._product_search_template_url = url
            self.run_meta["product_search_template"] = {
                "source": source,
                "affinity": affinity,
                "records_filtered": records_filtered,
                "product_count": product_count,
                "span": span,
                "score": list(score),
            }

    @staticmethod
    def _records_filtered_consistent(
        records_filtered: int | None,
        displayed_total: int | None,
    ) -> bool:
        """Reject only material catalogue contradictions, not small drift."""
        if records_filtered is None or displayed_total is None:
            return True
        try:
            records = int(records_filtered)
            displayed = int(displayed_total)
        except (TypeError, ValueError):
            return True

        if displayed <= 0:
            return records <= 0
        if records <= 0:
            return False

        # The storefront counter and productSearchV3 can differ slightly
        # because of listability/availability. A catastrophic mismatch such
        # as 1 vs 300 is never a valid template for that category.
        lower = max(1, int(displayed * 0.70))
        upper = max(displayed + 5, int(displayed * 1.40))
        return lower <= records <= upper

    def _select_best_product_search_template(
        self,
        displayed_total: int | None = None,
    ) -> str | None:
        """Re-rank captured candidates using live catalogue evidence."""
        if not self._product_search_candidates:
            return self._product_search_template_url

        candidates = list(self._product_search_candidates)
        response_candidates = [
            item for item in candidates
            if item.get("source") == "response"
            and item.get("records_filtered") is not None
        ]
        pool = response_candidates or candidates

        max_affinity = max(
            int(item.get("affinity") or 0)
            for item in pool
        )
        if max_affinity > 0:
            affinity_pool = [
                item for item in pool
                if int(item.get("affinity") or 0) == max_affinity
            ]
        else:
            affinity_pool = pool

        if displayed_total is not None and int(displayed_total) > 0:
            consistent = [
                item
                for item in affinity_pool
                if item.get("records_filtered") is not None
                and self._records_filtered_consistent(
                    item.get("records_filtered"),
                    displayed_total,
                )
            ]
            if consistent:
                affinity_pool = consistent

        def rank(item: dict) -> tuple:
            records = item.get("records_filtered")
            if displayed_total is not None and records is not None:
                distance = abs(int(records) - int(displayed_total))
            else:
                distance = 10**9

            return (
                int(item.get("affinity") or 0),
                1 if item.get("source") == "response" else 0,
                1
                if self._records_filtered_consistent(
                    item.get("records_filtered"),
                    displayed_total,
                )
                else 0,
                -distance,
                int(item.get("product_count") or -1),
                int(item.get("span") or 0),
            )

        best = max(affinity_pool, key=rank)
        url = best.get("url")
        if url:
            self._product_search_template_url = str(url)
            self._product_search_template_score = tuple(
                int(value)
                for value in best.get("score") or (-1, -1, -1, -1)
            )
            self.run_meta["product_search_template"] = {
                key: value
                for key, value in best.items()
                if key != "url"
            }
            self.run_meta["product_search_template_selection"] = {
                "displayed_total": displayed_total,
                "candidates": len(candidates),
                "response_candidates": len(response_candidates),
                "max_affinity": max_affinity,
            }
        return self._product_search_template_url

    def _refresh_product_search_template(
        self,
        page,
        category: Category,
        displayed_total: int | None,
    ) -> bool:
        """Reload the live category once and recapture its native VTEX search."""
        before = self._product_search_template_url
        try:
            response = page.reload(
                wait_until="domcontentloaded",
                timeout=90_000,
            )
            self._assert_not_blocked(
                page,
                response.status if response else None,
            )
            self._wait_for_catalog_ready(
                page,
                selector_timeout=35_000,
                settle_ms=max(self.wait_ms, 2_500),
            )
            page.wait_for_timeout(1_200)
        except Exception as exc:
            self.run_meta.setdefault(
                "product_search_template_refresh_errors",
                [],
            ).append(
                f"{type(exc).__name__}: {exc}"
            )
            return False

        selected = self._select_best_product_search_template(
            displayed_total,
        )
        self.run_meta.setdefault(
            "product_search_template_refreshes",
            [],
        ).append(
            {
                "category_id": category.id,
                "displayed_total": displayed_total,
                "changed": bool(selected and selected != before),
                "candidate_count": len(self._product_search_candidates),
            }
        )
        return bool(selected)

    def _capture_product_search_request(self, request) -> None:
        self._consider_product_search_template(
            request.url,
            source="request",
        )

    def _capture_product_search_response(self, response) -> None:
        url = response.url
        if "operationName=productSearchV3" not in url:
            return

        try:
            if response.status != 200:
                return
            payload = response.json()
        except Exception:
            return

        search = (
            (payload.get("data") or {}).get("productSearch")
            if isinstance(payload, dict)
            else None
        )
        if not isinstance(search, dict):
            return

        products = search.get("products") or []
        records_filtered = search.get("recordsFiltered")
        try:
            parsed_records = (
                int(records_filtered)
                if records_filtered is not None
                else None
            )
        except (TypeError, ValueError):
            parsed_records = None

        self._consider_product_search_template(
            url,
            records_filtered=parsed_records,
            product_count=(
                len(products)
                if isinstance(products, list)
                else None
            ),
            source="response",
        )

    def _collect_pages(self, page, category: Category, location: Location):
        self._product_search_template_url = None
        self._product_search_template_score = (-1, -1, -1, -1)
        self._product_search_category = category
        self._product_search_candidates = []
        page.on("request", self._capture_product_search_request)
        page.on("response", self._capture_product_search_response)
        rows = super()._collect_pages(page, category, location)
        self._select_best_product_search_template(
            self.run_meta.get("displayed_category_products")
        )
        self.run_meta["product_search_candidates"] = [
            {
                key: value
                for key, value in item.items()
                if key != "url"
            }
            for item in self._product_search_candidates[-50:]
        ]
        return rows

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

        try:
            page.wait_for_timeout(1_500)
        except Exception:
            pass

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

                    displayed_total = self.run_meta.get(
                        "displayed_category_products"
                    )
                    if not self._records_filtered_consistent(
                        records_filtered,
                        displayed_total,
                    ):
                        last_reason = (
                            "template_records_mismatch:"
                            f"{records_filtered}/{displayed_total}"
                        )
                        detail["reason"] = last_reason
                        detail["template_drift"] = True
                        attempts.append(detail)
                        break

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
                    page.wait_for_timeout(min(1_500 * attempt, 4_500))
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

        # A partial structured page is still trustworthy productSearchV3 data.
        # Do not discard the whole page merely because VTEX returned fewer
        # listable rows than the storefront counter expected. Overall coverage
        # is validated at the category level.
        if best_rows:
            info["accepted_partial"] = True
            self.run_meta.setdefault(
                "structured_partial_pages",
                [],
            ).append(
                {
                    "page": page_number,
                    "rows": len(best_rows),
                    "target_rows": target_rows,
                    "records_filtered": best_records_filtered,
                }
            )
            return best_rows, info

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

    def _load_page_rows(
        self,
        page,
        category,
        location,
        page_number: int,
        target_rows: int | None,
    ):
        url, html_rows, attempts = super()._load_page_rows(
            page,
            category,
            location,
            page_number,
            target_rows,
        )

        displayed_total = self._store_specific_total(page)
        if displayed_total is not None:
            self.run_meta["displayed_category_products"] = displayed_total

        # Select from all native productSearchV3 traffic observed while the
        # real storefront page was loading. This is deterministic and does not
        # depend on request ordering.
        self._select_best_product_search_template(displayed_total)

        api_rows, api_info = self._recover_page_from_product_search(
            page,
            category,
            location,
            page_number,
            target_rows=target_rows,
        )
        attempts.append(api_info)

        drift_reason = str(api_info.get("reason") or "")
        needs_refresh = (
            not api_rows
            and (
                drift_reason.startswith("template_records_mismatch:")
                or drift_reason == "template_not_captured"
            )
        )

        if needs_refresh:
            refreshed = self._refresh_product_search_template(
                page,
                category,
                displayed_total,
            )
            if refreshed:
                retry_rows, retry_info = (
                    self._recover_page_from_product_search(
                        page,
                        category,
                        location,
                        page_number,
                        target_rows=target_rows,
                    )
                )
                retry_info["after_template_refresh"] = True
                attempts.append(retry_info)
                if retry_rows:
                    api_rows = retry_rows
                    api_info = retry_info

        if api_rows:
            self.run_meta.setdefault(
                "api_price_pages",
                [],
            ).append(page_number)
            return url, api_rows, attempts

        # Once a structured productSearchV3 request has been captured, never
        # replace it with ambiguous HTML prices. A failed structured page stays
        # visible as an explicit quality failure instead of silently corrupting
        # the output.
        if self._product_search_template_url:
            self.run_meta.setdefault(
                "structured_price_missing_pages",
                [],
            ).append(
                {
                    "page": page_number,
                    "html_rows_rejected": len(html_rows),
                    "api_reason": api_info.get("reason"),
                    "api_status": api_info.get("status"),
                    "api_rows": api_info.get("rows"),
                    "api_attempts": len(
                        api_info.get("attempts") or []
                    ),
                    "template_refresh_attempted": needs_refresh,
                }
            )
            return url, [], attempts

        safe_html_rows = [
            row
            for row in html_rows
            if not self._html_row_has_category_facet_contamination(row)
        ]
        rejected = len(html_rows) - len(safe_html_rows)
        if rejected:
            self.run_meta.setdefault(
                "html_facet_rows_rejected",
                [],
            ).append(
                {"page": page_number, "rows": rejected}
            )
        return url, safe_html_rows, attempts


__all__ = [
    "ChedrauiBlocked",
    "ChedrauiStoreContextError",
    "ChedrauiScraper",
]
