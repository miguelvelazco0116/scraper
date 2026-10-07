from scrapy.http import HtmlResponse, Request

from scrapy_engine.spiders.ibarra_mayoreo import IbarraMayoreoSpider


def _html_response(url: str, html: str) -> HtmlResponse:
    request = Request(url)
    return HtmlResponse(
        url=url,
        request=request,
        body=html.encode("utf-8"),
        encoding="utf-8",
    )


def test_ibarra_scrapy_resolves_project_config():
    spider = IbarraMayoreoSpider(
        category="dentifricos-abarrotes",
        location="ibarra-online",
    )
    assert spider.category.id == "dentifricos-abarrotes"
    assert spider.location.id == "ibarra-online"
    assert spider.output_path.endswith(
        "ibarra_mayoreo_dentifricos-abarrotes.xlsx"
    )


def test_ibarra_scrapy_catalog_discovers_target_and_product_link():
    spider = IbarraMayoreoSpider(
        category="dentifricos-abarrotes",
        location="ibarra-online",
    )
    html = """
    <html><body>
      <a href="/catalogo/abarrotes/dentifricos/">Dentifricos (87)</a>
      <a href="?marca=TODAS&o=3&p=3">3</a>
      <div class="product-card">
        <a href="/crema-dental-demo" title="Crema Dental Demo">
          <img alt="Crema Dental Demo">
        </a>
        <span>$100.00</span>
        <button>Agregar al carrito</button>
      </div>
    </body></html>
    """
    response = _html_response(spider.category.url, html)

    output = list(spider.parse_catalog(response, page_number=1))
    requests = [item for item in output if isinstance(item, Request)]

    assert spider.target_products == 87
    assert spider.last_page == 3
    assert "https://ibarramayoreo.com/crema-dental-demo" in {
        request.url for request in requests
    }


def test_ibarra_scrapy_product_keeps_box_price():
    spider = IbarraMayoreoSpider(
        category="dentifricos-abarrotes",
        location="ibarra-online",
    )
    html = """
    <html><body>
      <h1>Crema Dental Demo</h1>
      <div>SKU: ABC123</div>
      <div>Marca: DEMO</div>
      <div>Presentación: Caja - 12 artículos. $240.00</div>
      <button>Agregar al carrito</button>
    </body></html>
    """
    response = _html_response(
        "https://ibarramayoreo.com/crema-dental-demo",
        html,
    )

    rows = list(
        spider.parse_product(
            response,
            fallback_title="Crema Dental Demo",
        )
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["sku"] == "ABC123"
    assert row["price_current"] == 240.0
    assert row["price_regular"] == 240.0
    assert row["price_raw"] == "CAJA | 12 artículos por caja | $240.00 MXN"
    assert row["url"] == "https://ibarramayoreo.com/crema-dental-demo"


def test_ibarra_scrapy_repairs_mojibake_in_pdp():
    spider = IbarraMayoreoSpider(
        category="perfumeria-abarrotes",
        location="ibarra-online",
    )
    html = """
    <html><body>
      <h1>Desodorante Obao Mujer Rosa TentaciÃ³n Aerosol 150 ml</h1>
      <div>Disponible</div>
      <div>SKU: 46222</div>
      <div>Marca: OBAO</div>
      <div>4 de 5</div>
      <div>PresentaciÃ³n: Caja - 12 artÃ­culo(s).</div>
      <div>$576.00 MXN</div>
    </body></html>
    """
    response = _html_response(
        "https://ibarramayoreo.com/desodorante-obao-demo",
        html,
    )

    detail = spider._parse_pdp_detail(
        response,
        "Desodorante Obao Mujer Rosa TentaciÃ³n Aerosol 150 ml",
    )

    assert detail["sku"] == "46222"
    assert detail["box_units"] == 12
    assert detail["box_price"] == 576.0


def test_ibarra_scrapy_structural_card_without_price_signal_is_discovered():
    spider = IbarraMayoreoSpider(
        category="perfumeria-abarrotes",
        location="ibarra-online",
    )
    html = """
    <html><body>
      <div class="product-card">
        <a href="/producto-sin-precio-en-card" title="Producto sin precio">
          <img alt="Producto sin precio">
        </a>
      </div>
    </body></html>
    """
    response = _html_response(spider.category.url, html)

    links = spider._root_product_links(response)

    assert len(links) == 1
    assert links[0]["href"] == (
        "https://ibarramayoreo.com/producto-sin-precio-en-card"
    )
    assert links[0]["title"] == "Producto sin precio"
    assert "card_text" in links[0]
