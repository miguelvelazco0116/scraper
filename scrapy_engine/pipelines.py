from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from openpyxl.styles import Font

from main import COLUMNS, CONSOLIDATED_PATH, deduplicate_catalog, update_consolidated_output
from scraper.io_utils import atomic_output_path


def _as_bool(value) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "si", "sí"}


class CanonicalExcelPipeline:
    """Collect Scrapy items and write the project's canonical Excel schema."""

    def __init__(self, crawler) -> None:
        self.crawler = crawler
        self.rows: list[dict] = []

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    def process_item(self, item):
        row = dict(item)
        for column in COLUMNS:
            row.setdefault(column, None)
        self.rows.append({column: row.get(column) for column in COLUMNS})
        return item

    def close_spider(self) -> None:
        spider = self.crawler.spider
        frame = pd.DataFrame(self.rows)
        for column in COLUMNS:
            if column not in frame.columns:
                frame[column] = None
        frame = frame[COLUMNS]
        frame = deduplicate_catalog(frame)

        target = getattr(spider, "target_products", None)
        products = len(frame)
        coverage = (
            products / int(target)
            if target not in (None, 0)
            else None
        )
        sku_complete = (
            int(frame["sku"].fillna("").astype(str).str.strip().ne("").sum())
            if not frame.empty
            else 0
        )
        price_complete = (
            int(frame["price_current"].notna().sum())
            if not frame.empty
            else 0
        )
        url_complete = (
            int(frame["url"].fillna("").astype(str).str.strip().ne("").sum())
            if not frame.empty
            else 0
        )

        if frame.empty:
            availability = pd.Series(dtype="object")
        else:
            availability = (
                frame["availability_status"]
                .fillna("UNKNOWN")
                .astype(str)
                .str.strip()
                .str.upper()
                .replace("", "UNKNOWN")
            )
        price_required_mask = ~availability.eq("UNAVAILABLE")
        price_required_products = int(price_required_mask.sum())
        price_required_complete = (
            int(
                frame.loc[
                    price_required_mask,
                    "price_current",
                ].notna().sum()
            )
            if not frame.empty
            else 0
        )
        numeric_current = pd.to_numeric(
            frame["price_current"],
            errors="coerce",
        )
        numeric_regular = pd.to_numeric(
            frame["price_regular"],
            errors="coerce",
        )
        price_required_valid = (
            int(
                (
                    price_required_mask
                    & numeric_current.notna()
                    & numeric_current.gt(0)
                ).sum()
            )
            if not frame.empty
            else 0
        )
        price_order_errors = (
            int(
                (
                    numeric_current.notna()
                    & numeric_regular.notna()
                    & numeric_regular.lt(numeric_current)
                ).sum()
            )
            if not frame.empty
            else 0
        )
        product_complete = (
            int(
                frame["product"]
                .fillna("")
                .astype(str)
                .str.strip()
                .ne("")
                .sum()
            )
            if not frame.empty
            else 0
        )

        identifier_required_mask = ~availability.eq("UNAVAILABLE")
        identifier_required_products = int(
            identifier_required_mask.sum()
        )
        sku_required_complete = (
            int(
                frame.loc[
                    identifier_required_mask,
                    "sku",
                ]
                .fillna("")
                .astype(str)
                .str.strip()
                .ne("")
                .sum()
            )
            if not frame.empty
            else 0
        )
        url_required_complete = (
            int(
                frame.loc[
                    identifier_required_mask,
                    "url",
                ]
                .fillna("")
                .astype(str)
                .str.strip()
                .ne("")
                .sum()
            )
            if not frame.empty
            else 0
        )
        require_sku = bool(getattr(spider, "require_sku", True))
        require_url = bool(getattr(spider, "require_url", True))
        identifier_clean = (
            (not require_sku or sku_required_complete == identifier_required_products)
            and (
                not require_url
                or url_required_complete == identifier_required_products
            )
        )

        minimum_coverage = float(
            getattr(spider, "minimum_coverage", 1.0) or 1.0
        )
        missing_price_products = list(
            getattr(
                spider,
                "missing_price_products",
                getattr(spider, "no_box_products", []),
            )
            or []
        )
        failed_product_requests = list(
            getattr(spider, "failed_product_requests", []) or []
        )
        parse_errors = int(getattr(spider, "parse_errors", 0) or 0)
        clean_run = (
            price_required_complete == price_required_products
            and price_required_valid == price_required_products
            and price_order_errors == 0
            and product_complete == products
            and identifier_clean
            and not missing_price_products
            and not failed_product_requests
            and parse_errors == 0
        )

        if products <= 0:
            quality_status = "EMPTY"
        elif target is None:
            quality_status = "TARGET_UNKNOWN"
        elif products >= int(target) and clean_run:
            quality_status = "COMPLETE"
        elif (
            coverage is not None
            and coverage >= minimum_coverage
            and clean_run
        ):
            quality_status = "SAMPLE_ACCEPTED"
        else:
            quality_status = "PARTIAL"

        output_path = Path(
            getattr(
                spider,
                "output_path",
                f"output/scrapy/{spider.name}.xlsx",
            )
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)

        discovered_link_products = len(
            getattr(spider, "discovery_links", set()) or set()
        )
        catalog_orphan_rows = list(
            getattr(spider, "catalog_orphan_rows", []) or []
        )
        discovered_from_rows = (
            discovered_link_products + len(catalog_orphan_rows)
        )
        discovered_override = getattr(
            spider,
            "discovered_products",
            None,
        )
        discovered_products = (
            max(discovered_from_rows, int(discovered_override))
            if discovered_override is not None
            else discovered_from_rows
        )
        discovery_source = getattr(
            spider,
            "discovery_source",
            None,
        )
        parsed_product_pages = len(
            getattr(spider, "parsed_product_urls", set()) or set()
        )
        yielded_product_pages = len(
            getattr(spider, "yielded_product_urls", set()) or set()
        )
        summary = pd.DataFrame(
            [
                {
                    "retailer": (
                        frame.iloc[0]["retailer"]
                        if not frame.empty
                        else getattr(spider, "retailer_label", spider.name)
                    ),
                    "category_id": getattr(spider, "category_id", None),
                    "products": products,
                    "target_products": target,
                    "coverage": coverage,
                    "quality_status": quality_status,
                    "minimum_coverage": minimum_coverage,
                    "sku_complete": sku_complete,
                    "price_complete": price_complete,
                    "price_required_products": price_required_products,
                    "price_required_complete": price_required_complete,
                    "price_required_valid": price_required_valid,
                    "price_order_errors": price_order_errors,
                    "product_complete": product_complete,
                    "identifier_required_products": (
                        identifier_required_products
                    ),
                    "sku_required_complete": sku_required_complete,
                    "url_required_complete": url_required_complete,
                    "require_sku": require_sku,
                    "require_url": require_url,
                    "url_complete": url_complete,
                    "requests": getattr(spider, "api_pages", None),
                    "discovered_products": discovered_products or None,
                    "discovery_source": discovery_source,
                    "discovered_link_products": (
                        discovered_link_products or None
                    ),
                    "catalog_orphan_products": len(catalog_orphan_rows),
                    "parsed_product_pages": parsed_product_pages or None,
                    "yielded_product_pages": yielded_product_pages or None,
                    "missing_price_products": len(
                        missing_price_products
                    ),
                    "failed_product_requests": len(
                        failed_product_requests
                    ),
                    "parse_errors": parse_errors,
                    "engine": "Scrapy",
                }
            ]
        )

        with atomic_output_path(output_path) as temporary_output:
            with pd.ExcelWriter(
                temporary_output,
                engine="openpyxl",
            ) as writer:
                frame.to_excel(
                    writer,
                    index=False,
                    sheet_name="Concentrado",
                )
                summary.to_excel(
                    writer,
                    index=False,
                    sheet_name="Resumen",
                )

                for sheet_name in ("Concentrado", "Resumen"):
                    ws = writer.book[sheet_name]
                    ws.freeze_panes = "A2"
                    ws.auto_filter.ref = ws.dimensions
                    for cell in ws[1]:
                        cell.font = Font(bold=True)

        diagnostics_dir = Path("diagnostics") / "scrapy"
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        diagnostics_path = diagnostics_dir / (
            f"{spider.name}_{getattr(spider, 'category_id', 'unknown')}.json"
        )
        missing_discovery = None
        if target not in (None, 0):
            missing_discovery = max(
                int(target) - discovered_products,
                0,
            )
        catalog_pages = list(
            getattr(spider, "catalog_pages", []) or []
        )
        product_candidates = dict(
            getattr(spider, "product_candidates", {}) or {}
        )
        normalized_discovered = {
            str(url).rstrip("/") for url in product_candidates
        }
        normalized_parsed = {
            str(url).rstrip("/")
            for url in getattr(
                spider,
                "parsed_product_urls",
                set(),
            )
        }
        normalized_yielded = {
            str(url).rstrip("/")
            for url in getattr(
                spider,
                "yielded_product_urls",
                set(),
            )
        }
        undispatched_or_unreceived = sorted(
            normalized_discovered - normalized_parsed
        )
        parsed_without_row = sorted(
            normalized_parsed - normalized_yielded
        )

        diagnostics = {
            "spider": spider.name,
            "category_id": getattr(spider, "category_id", None),
            "target_products": target,
            "products": products,
            "coverage": coverage,
            "quality_status": quality_status,
            "minimum_coverage": minimum_coverage,
            "product_complete": product_complete,
            "price_required_products": price_required_products,
            "price_required_complete": price_required_complete,
            "price_required_valid": price_required_valid,
            "price_order_errors": price_order_errors,
            "identifier_required_products": (
                identifier_required_products
            ),
            "sku_required_complete": sku_required_complete,
            "url_required_complete": url_required_complete,
            "require_sku": require_sku,
            "require_url": require_url,
            "discovered_products": discovered_products,
            "discovery_source": discovery_source,
            "discovered_link_products": discovered_link_products,
            "catalog_orphan_products": catalog_orphan_rows,
            "missing_from_discovery": missing_discovery,
            "parsed_product_pages": parsed_product_pages,
            "yielded_product_pages": yielded_product_pages,
            "missing_price_products": missing_price_products,
            "failed_product_requests": failed_product_requests,
            "parse_errors": parse_errors,
            "catalog_pages": catalog_pages,
            "undispatched_or_unreceived": [
                product_candidates.get(url, {"url": url})
                for url in undispatched_or_unreceived
            ],
            "parsed_without_row": [
                product_candidates.get(url, {"url": url})
                for url in parsed_without_row
            ],
        }
        with atomic_output_path(diagnostics_path) as temporary_json:
            temporary_json.write_text(
                json.dumps(
                    diagnostics,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        update_requested = _as_bool(
            getattr(spider, "update_consolidated", False)
        )
        consolidated_updated = False
        if update_requested and quality_status in {
            "COMPLETE",
            "SAMPLE_ACCEPTED",
        }:
            update_consolidated_output(frame, CONSOLIDATED_PATH)
            consolidated_updated = True

        spider.scrapy_result = {
            "output": str(output_path),
            "products": products,
            "target_products": target,
            "coverage": coverage,
            "quality_status": quality_status,
            "minimum_coverage": minimum_coverage,
            "sku_complete": sku_complete,
            "price_complete": price_complete,
            "price_required_products": price_required_products,
            "price_required_complete": price_required_complete,
            "price_required_valid": price_required_valid,
            "price_order_errors": price_order_errors,
            "product_complete": product_complete,
            "identifier_required_products": (
                identifier_required_products
            ),
            "sku_required_complete": sku_required_complete,
            "url_required_complete": url_required_complete,
            "require_sku": require_sku,
            "require_url": require_url,
            "url_complete": url_complete,
            "discovered_products": discovered_products,
            "discovery_source": discovery_source,
            "discovered_link_products": discovered_link_products,
            "catalog_orphan_products": len(catalog_orphan_rows),
            "parsed_product_pages": parsed_product_pages,
            "yielded_product_pages": yielded_product_pages,
            "missing_price_products": len(
                missing_price_products
            ),
            "failed_product_requests": len(
                failed_product_requests
            ),
            "parse_errors": parse_errors,
            "catalog_pages": len(catalog_pages),
            "missing_pdp_count": len(
                undispatched_or_unreceived
            ),
            "parsed_without_row_count": len(
                parsed_without_row
            ),
            "diagnostics": str(diagnostics_path),
            "consolidated_updated": consolidated_updated,
        }

        spider.logger.info(
            "SCRAPY_RESULT products=%s target=%s coverage=%s "
            "quality=%s price_required=%s/%s discovered=%s parsed=%s "
            "missing_price=%s failed=%s output=%s consolidated=%s",
            products,
            target,
            coverage,
            quality_status,
            price_required_complete,
            price_required_products,
            discovered_products,
            parsed_product_pages,
            len(missing_price_products),
            len(failed_product_requests),
            output_path,
            consolidated_updated,
        )
