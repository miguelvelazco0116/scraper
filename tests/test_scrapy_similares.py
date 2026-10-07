from scrapy.http import HtmlResponse, Request

from scrapy_engine.spiders.farmacias_similares import (
    FarmaciasSimilaresSpider,
)


def _html_response(url: str, html: str) -> HtmlResponse:
    request = Request(url)
    return HtmlResponse(
        url=url,
        request=request,
        body=html.encode("utf-8"),
        encoding="utf-8",
    )


def test_similares_scrapy_resolves_project_config():
    spider = FarmaciasSimilaresSpider(
        category="condones",
        location="similares-online",
    )

    assert spider.category.id == "condones"
    assert spider.location.id == "similares-online"
    assert spider.output_path.endswith(
        "farmacias_similares_condones.xlsx"
    )


def test_similares_scrapy_catalog_reads_target_and_promo_card():
    spider = FarmaciasSimilaresSpider(
        category="condones",
        location="similares-online",
    )
    html = """
    <html><body>
      <h1>Condones</h1>
      <div>9 productos</div>
      <article class="product-card">
        <a href="/alfa_condon_demo/p" title="CONDON ALFA DEMO">
          CONDON ALFA DEMO
        </a>
        <span>-25%</span>
        <span>$39.00</span>
        <span>$29.25</span>
        <button>Comprar ahora</button>
      </article>
    </body></html>
    """
    response = _html_response(spider.category.url, html)

    output = list(spider.parse_catalog(response, page_number=1))
    requests = [item for item in output if isinstance(item, Request)]

    assert spider.target_products == 9
    assert len(spider.discovery_links) == 1

    product_request = next(
        request
        for request in requests
        if request.url.endswith("/alfa_condon_demo/p")
    )
    item = product_request.cb_kwargs["catalog_item"]

    assert item["title"] == "CONDON ALFA DEMO"
    assert item["price_regular"] == 39.0
    assert item["price_current"] == 29.25
    assert item["promotion"] == "-25%"
    assert item["availability_status"] == "AVAILABLE"


def test_similares_scrapy_pdp_combines_catalog_promo_with_sku():
    spider = FarmaciasSimilaresSpider(
        category="condones",
        location="similares-online",
    )
    url = "https://www.farmaciasdesimilares.com/alfa_condon_demo/p"
    html = """
    <html><body>
      <h1>CONDON ALFA DEMO</h1>
      <div>Referencia: 2971</div>
      <div>$39.00</div>
      <button>Comprar ahora</button>
    </body></html>
    """
    response = _html_response(url, html)
    catalog_item = {
        "href": url,
        "title": "CONDON ALFA DEMO",
        "card_text": "-25% CONDON ALFA DEMO $39.00 $29.25 Comprar ahora",
        "price_current": 29.25,
        "price_regular": 39.0,
        "promotion": "-25%",
        "availability_status": "AVAILABLE",
        "is_available": True,
        "availability_raw": "Comprar ahora",
    }

    rows = list(
        spider.parse_product(
            response,
            catalog_item=catalog_item,
        )
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["sku"] == "2971"
    assert row["price_current"] == 29.25
    assert row["price_regular"] == 39.0
    assert row["promotion"] == "-25%"
    assert row["availability_status"] == "AVAILABLE"
    assert row["url"] == url


def test_similares_scrapy_keeps_explicit_unavailable_card_without_pdp():
    spider = FarmaciasSimilaresSpider(
        category="aparato-respiratorio",
        location="similares-online",
    )
    html = """
    <html><body>
      <div class="product-card">
        <h3>STERIMAR DEMO 100 ML</h3>
        <div>Agotado</div>
      </div>
    </body></html>
    """
    response = _html_response(spider.category.url, html)

    items = spider._unavailable_cards_without_pdp(response)

    assert len(items) == 1
    assert items[0]["title"] == "STERIMAR DEMO 100 ML"
    assert items[0]["availability_status"] == "UNAVAILABLE"

    row = spider._unavailable_row(items[0])
    assert row["price_current"] is None
    assert row["sku"] is None
    assert row["url"] is None
    assert row["availability_status"] == "UNAVAILABLE"


def test_similares_scrapy_page_url_preserves_category_and_sets_page():
    spider = FarmaciasSimilaresSpider(
        category="aparato-respiratorio",
        location="similares-online",
    )

    page2 = FarmaciasSimilaresSpider.__mro__[1]
    del page2

    url = FarmaciasSimilaresSpider.__dict__
    del url

    page_url = (
        __import__(
            "scraper.retailers.farmacias_similares",
            fromlist=["FarmaciasSimilaresScraper"],
        )
        .FarmaciasSimilaresScraper
        ._page_url(spider.category.url, 2)
    )

    assert page_url.startswith(spider.category.url)
    assert "page=2" in page_url
