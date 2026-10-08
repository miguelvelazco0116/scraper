import json

from scrapy.http import TextResponse

from scrapy_engine.spiders.farmacias_san_pablo import (
    FarmaciasSanPabloSpider,
)


def _json_response(request, payload: dict) -> TextResponse:
    body = json.dumps(payload).encode("utf-8")
    return TextResponse(
        url=request.url,
        request=request,
        body=body,
        encoding="utf-8",
        headers={"Content-Type": "application/json"},
    )


def test_san_pablo_scrapy_resolves_project_config():
    spider = FarmaciasSanPabloSpider(
        category="enjuagues-bucales",
        location="san-pablo-online",
    )

    assert spider.category.id == "enjuagues-bucales"
    assert spider.location.id == "san-pablo-online"
    assert spider.category_code == "030040003"
    assert spider.rows_per_page == 48
    assert spider.output_path.endswith(
        "farmacias_san_pablo_enjuagues-bucales.xlsx"
    )


def test_san_pablo_scrapy_category_code_uses_last_numeric_segment():
    url = (
        "https://www.farmaciasanpablo.com.mx/"
        "salud-natural/salud-natural/tes/c/Pastas-dentales0/"
        "c/030040007"
    )
    assert FarmaciasSanPabloSpider._category_code(url) == "030040007"


def test_san_pablo_scrapy_builds_occ_query():
    url = FarmaciasSanPabloSpider._api_url(
        "030040003",
        page_index=2,
        page_size=48,
    )

    assert "search-sponsored" in url
    assert "pageSize=48" in url
    assert "currentPage=2" in url
    assert "030040003" in url


def test_san_pablo_scrapy_occ_parse_yields_canonical_row():
    spider = FarmaciasSanPabloSpider(
        category="enjuagues-bucales",
        location="san-pablo-online",
    )
    request = spider._request(0)
    payload = {
        "pagination": {
            "totalResults": 1,
            "totalPages": 1,
        },
        "products": [
            {
                "code": "000000000000700142",
                "name": "Sterimar Nasal 100 ml",
                "url": (
                    "/medicamentos/gripe-y-tos/descongestionantes/"
                    "sterimar-nasal/p/000000000000700142"
                ),
                "price": {"value": 223.0},
                "basePrice": {"value": 319.0},
                "potentialPromotions": [
                    {"description": "30% de descuento"}
                ],
                "gtmProperties": {"brand": "Stérimar"},
                "stock": {
                    "stockLevelStatus": "inStock",
                    "stockLevel": 7,
                },
            }
        ],
    }
    response = _json_response(request, payload)

    output = list(spider.parse_api(response, page_index=0))
    rows = [item for item in output if isinstance(item, dict)]

    assert spider.target_products == 1
    assert spider.total_pages == 1
    assert spider.discovered_products == 1
    assert len(rows) == 1

    row = rows[0]
    assert row["sku"] == "700142"
    assert row["price_current"] == 223.0
    assert row["price_regular"] == 319.0
    assert row["availability_status"] == "AVAILABLE"


def test_san_pablo_scrapy_occ_retains_unavailable_without_price():
    spider = FarmaciasSanPabloSpider(
        category="enjuagues-bucales",
        location="san-pablo-online",
    )
    request = spider._request(0)
    payload = {
        "pagination": {
            "totalResults": 1,
            "totalPages": 1,
        },
        "products": [
            {
                "code": "000000000000999999",
                "name": "Producto agotado",
                "url": "/producto-agotado/p/000000000000999999",
                "price": None,
                "basePrice": None,
                "stock": {
                    "stockLevelStatus": "outOfStock",
                    "stockLevel": 0,
                },
            }
        ],
    }
    response = _json_response(request, payload)

    rows = [
        item
        for item in spider.parse_api(response, page_index=0)
        if isinstance(item, dict)
    ]

    assert len(rows) == 1
    assert rows[0]["price_current"] is None
    assert rows[0]["availability_status"] == "UNAVAILABLE"
    assert spider.missing_price_products == []
