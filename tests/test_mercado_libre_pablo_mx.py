from decimal import Decimal
import unittest

from bs4 import BeautifulSoup
from scraper.retailers.mercado_libre_pablo_mx import (
    MercadoLibreScraper, MercadoLibrePageError, parse_mxn
)

CARD = """<li class="ui-search-layout__item">
<a href="https://articulo.mercadolibre.com.mx/MLM-123-test"><h2>Producto México</h2></a>
<span class="andes-money-amount__fraction">1,234</span>
<span class="andes-money-amount__cents">50</span>
<img src="https://img.example/image.png"></li>"""

class TestPabloMx(unittest.TestCase):
    def test_mexico_url(self):
        self.assertEqual(MercadoLibreScraper.search_url("pasta dental", 2),
            "https://listado.mercadolibre.com.mx/pasta-dental_Desde_51_NoIndex_True")

    def test_mxn(self):
        self.assertEqual(parse_mxn("$1,234.50"), Decimal("1234.50"))
        self.assertEqual(parse_mxn("$ 1.234,50"), Decimal("1234.50"))

    def test_card_and_dedup(self):
        page = "<ul>" + CARD + CARD + "</ul>"
        result = MercadoLibreScraper.parse_pages([page], requested_limit=20)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].price, Decimal("1234.50"))

    def test_empty_page_cannot_pass(self):
        with self.assertRaises(MercadoLibrePageError):
            MercadoLibreScraper.scrape_page_results(BeautifulSoup("<html/>", "html.parser"))

    def test_external_links_rejected(self):
        page = CARD.replace("articulo.mercadolibre.com.mx", "evil.example")
        with self.assertRaises(MercadoLibrePageError):
            MercadoLibreScraper.scrape_page_results(BeautifulSoup(page, "html.parser"))

    def test_product_detail(self):
        page = """<h1 class="ui-pdp-title">Producto</h1>
        <span class="andes-money-amount__fraction">299</span>
        <link rel="canonical" href="https://articulo.mercadolibre.com.mx/MLM-123-test">"""
        data = MercadoLibreScraper.scrape_product_details(BeautifulSoup(page, "html.parser"))
        self.assertEqual(data.title, "Producto")
        self.assertEqual(data.price, Decimal("299"))

if __name__ == "__main__":
    unittest.main()
