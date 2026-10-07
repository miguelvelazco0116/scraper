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


SPIDER_BY_RETAILER = {
    "farmacias-del-ahorro": FarmaciasDelAhorroSpider,
}

DEFAULT_LOCATION = {
    "farmacias-del-ahorro": "fahorro-online",
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

    process.crawl(
        spider_cls,
        category=args.category,
        location=location,
        max_pages=args.max_pages,
        rows_per_page=args.rows_per_page,
        update_consolidated=args.update_consolidated,
    )
    process.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
