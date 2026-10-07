from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("SCRAPY_SETTINGS_MODULE", "scrapy_engine.settings")

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

from scrapy_engine.spiders.farmacias_del_ahorro import (
    FarmaciasDelAhorroSpider,
)
from scrapy_engine.spiders.ibarra_mayoreo import IbarraMayoreoSpider


SPIDER_BY_RETAILER = {
    "farmacias-del-ahorro": FarmaciasDelAhorroSpider,
    "ibarra-mayoreo": IbarraMayoreoSpider,
}

DEFAULT_LOCATION = {
    "farmacias-del-ahorro": "fahorro-online",
    "ibarra-mayoreo": "ibarra-online",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Runner Scrapy para retailers migrados."
    )
    parser.add_argument(
        "--retailer",
        required=True,
        choices=sorted(SPIDER_BY_RETAILER),
    )
    parser.add_argument("--category", required=True)
    parser.add_argument("--location", default=None)
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--rows-per-page", type=int, default=50)
    parser.add_argument("--update-consolidated", action="store_true")
    args = parser.parse_args()

    spider_cls = SPIDER_BY_RETAILER[args.retailer]
    spider_name = spider_cls.name
    location = args.location or DEFAULT_LOCATION[args.retailer]

    settings = get_project_settings()
    process = CrawlerProcess(settings)

    print("=" * 78)
    print("SCRAPY - SCRAPER MULTI-RETAILER")
    print("=" * 78)
    print(f"Retailer   : {args.retailer}")
    print(f"Spider     : {spider_name}")
    print(f"Categoria  : {args.category}")
    print(f"Ubicacion  : {location}")
    print(f"Max pages  : {args.max_pages}")
    print(f"Rows/page  : {args.rows_per_page}")
    print(
        "Consolidado: "
        + ("si, solo si COMPLETE" if args.update_consolidated else "no")
    )
    print("")

    crawler = process.create_crawler(spider_cls)
    process.crawl(
        crawler,
        category=args.category,
        location=location,
        max_pages=args.max_pages,
        rows_per_page=args.rows_per_page,
        update_consolidated=args.update_consolidated,
    )
    process.start()

    finish_reason = str(
        crawler.stats.get_value("finish_reason") or ""
    ).strip()
    result = getattr(crawler.spider, "scrapy_result", {}) or {}
    quality = str(result.get("quality_status") or "").strip().upper()
    products = int(result.get("products") or 0)
    target = result.get("target_products")
    coverage = result.get("coverage")

    print("")
    print("-" * 78)
    print("RESULTADO SCRAPY")
    print("-" * 78)
    print(f"Status       : {quality or finish_reason or 'UNKNOWN'}")
    print(f"Productos    : {products}")
    print(f"Target       : {target}")
    print(f"Cobertura    : {coverage}")
    print(f"Finish reason: {finish_reason}")
    print(f"Descubiertos : {result.get('discovered_products')}")
    print(f"PDP recibidos: {result.get('parsed_product_pages')}")
    print(f"Sin CAJA     : {result.get('no_box_products')}")
    print(f"PDP fallidos : {result.get('failed_product_requests')}")
    print(f"Parse errors : {result.get('parse_errors')}")
    print(f"PDP faltantes: {result.get('missing_pdp_count')}")
    print(
        f"Sin fila final: "
        f"{result.get('parsed_without_row_count')}"
    )
    print(f"Diagnostico  : {result.get('diagnostics')}")
    print(f"Output       : {result.get('output')}")
    print(
        "Consolidado : "
        + (
            "actualizado"
            if result.get("consolidated_updated")
            else "sin cambios"
        )
    )

    if finish_reason == "blocked":
        return 2
    if quality == "COMPLETE":
        return 0
    if quality == "EMPTY":
        return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
