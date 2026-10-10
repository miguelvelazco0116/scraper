from playwright.sync_api import Error as PlaywrightError

from scraper.retailers.ibarra_mayoreo import IbarraMayoreoScraper


class _FakeResponse:
    status = 200


class _FakePage:
    def __init__(self):
        self.calls = 0
        self.waits = []

    def goto(self, url, wait_until, timeout):
        self.calls += 1
        if self.calls < 3:
            raise PlaywrightError("transient navigation failure")
        return _FakeResponse()

    def wait_for_timeout(self, value):
        self.waits.append(value)

    def title(self):
        return ""

    def locator(self, _selector):
        return _FakeLocator()


class _FakeLocator:
    def inner_text(self, timeout):
        return ""


def test_ibarra_goto_retries_transient_playwright_errors():
    scraper = IbarraMayoreoScraper(wait_ms=700)
    page = _FakePage()

    scraper._goto(page, "https://ibarramayoreo.com/producto")

    assert page.calls == 3
    assert any(wait >= 1500 for wait in page.waits)


def test_ibarra_filters_promotional_navigation_links():
    assert not IbarraMayoreoScraper._is_product_candidate(
        {"title": "DESTACADOS", "href": "https://ibarramayoreo.com/destacados"}
    )
    assert not IbarraMayoreoScraper._is_product_candidate(
        {"title": "BONUS", "href": "https://ibarramayoreo.com/bonus"}
    )
    assert IbarraMayoreoScraper._is_product_candidate(
        {
            "title": "Detergente Mi Genio Multiusos 9 kg",
            "href": "https://ibarramayoreo.com/detergente-mi-genio-multiusos-9-kg",
        }
    )


def test_ibarra_parses_box_and_single_item_prices():
    body = """
    Crema Dental Colgate Triple Acción 75 ml
    SKU: 22601
    Marca: COLGATE
    Presentación: Caja - 72 artículo(s).
    $1,826.40 MXN
    Presentación: Pieza - 1 artículo(s).
    $26.60 MXN
    """

    detail = IbarraMayoreoScraper._parse_box_detail(
        body,
        "Crema Dental Colgate Triple Acción 75 ml",
    )

    assert detail["box_units"] == 72
    assert detail["box_price"] == 1826.40
    assert detail["sale_presentation"] == "CAJA"
    assert detail["sale_units"] == 72
    assert detail["price_per_unit"] == round(1826.40 / 72, 4)
    assert detail["single_item_presentation"] == "Pieza"
    assert detail["single_item_price"] == 26.60
    assert detail["is_single_item"] is False


def test_ibarra_accepts_single_only_product():
    body = """
    Producto Individual
    SKU: 10001
    Marca: MARCA
    Presentación: Pieza - 1 artículo(s).
    $25.00 MXN
    """

    detail = IbarraMayoreoScraper._parse_box_detail(
        body,
        "Producto Individual",
    )

    assert detail["box_price"] is None
    assert detail["sale_presentation"] == "PIEZA"
    assert detail["sale_units"] == 1
    assert detail["sale_price"] == 25.00
    assert detail["price_per_unit"] == 25.00
    assert detail["single_item_price"] == 25.00
    assert detail["is_single_item"] is True


def test_ibarra_supports_package_as_single_presentation():
    body = """
    Manteca Inca 250 g
    SKU: 259
    Marca: INCA
    Presentación: Caja - 48 artículo(s).
    $1,098.70 MXN
    Presentación: Paquete - 1 artículo(s).
    $24.00 MXN
    """

    detail = IbarraMayoreoScraper._parse_box_detail(
        body,
        "Manteca Inca 250 g",
    )

    assert detail["sale_units"] == 48
    assert detail["single_item_presentation"] == "Paquete"
    assert detail["single_item_price"] == 24.00
    assert detail["price_per_unit"] == round(1098.70 / 48, 4)
