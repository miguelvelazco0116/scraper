from scrapy.http import HtmlResponse, Request, TextResponse

from scrapy_engine.spiders.farmacias_del_ahorro import (
    FarmaciasDelAhorroSpider,
)


def test_scrapy_fda_spider_resolves_project_config():
    spider = FarmaciasDelAhorroSpider(
        category="preservativos",
        location="fahorro-online",
    )
    assert spider.category.id == "preservativos"
    assert spider.location.id == "fahorro-online"
    assert spider.output_path.endswith(
        "farmacias_del_ahorro_preservativos.xlsx"
    )


def test_scrapy_fda_category_parse_builds_empathy_request():
    spider = FarmaciasDelAhorroSpider(
        category="congestion-nasal",
        location="fahorro-online",
    )
    html = b"""
    <script>
    {
      "component":"Infinite_EmpathySearch/js/view/search-list-component",
      "categoryId":"8196"
    }
    </script>
    """
    request = Request(spider.category.url)
    response = HtmlResponse(
        url=spider.category.url,
        request=request,
        body=html,
        encoding="utf-8",
    )

    requests = list(spider.parse_category(response))
    assert len(requests) == 1
    assert "api.empathy.co" in requests[0].url
    assert "browseValue=8196" in requests[0].url


def test_scrapy_fda_api_parse_yields_canonical_row():
    spider = FarmaciasDelAhorroSpider(
        category="cremas-dentales",
        location="fahorro-online",
    )
    spider.numeric_category_id = "8196"

    payload = b"""
    {
      "catalog": {
        "pagination": {"total": 1},
        "content": [
          {
            "sku": "123",
            "ecommTitle": "Crema Dental Colgate Total",
            "ecommBrand": "COLGATE",
            "ecommUrlKey": "crema-dental-colgate-total",
            "currentPrice": 50,
            "previousPrice": 60
          }
        ]
      }
    }
    """
    request = Request(
        "https://api.empathy.co/test",
        meta={"start": 0, "page_index": 0},
    )
    response = TextResponse(
        url=request.url,
        request=request,
        body=payload,
        encoding="utf-8",
        headers={"Content-Type": "application/json"},
    )

    output = list(spider.parse_api(response))
    rows = [item for item in output if isinstance(item, dict)]

    assert len(rows) == 1
    assert rows[0]["retailer"] == "Farmacias del Ahorro"
    assert rows[0]["sku"] == "123"
    assert rows[0]["price_current"] == 50.0
    assert spider.target_products == 1
