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
