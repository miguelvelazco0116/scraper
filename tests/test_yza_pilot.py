"""Offline tests for Farmacias YZA, online catalogue with NO locality verification.

Never create production master output, falsely attribute CP or store,
or require a postal input. CDP attaches to an already-open operator tab.
"""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from scraper.config import load_categories, load_locations
from scraper.retailers.yza import (
    YzaContextError, YzaScrapeError, YzaScraper, discover_cards,
    open_online_category, page_url, prices_from_card, product_sku,
    yza_browser_page,
)


def test_yza_has_exactly_two_requested_categories():
    categories = load_categories("config/yza/categories.yaml")
    assert {c.id: c.url for c in categories} == {
        "medicamentos-respiratorio":
            "https://www.yza.mx/medicamentos/respiratorio/",
        "higiene-bucal":
            "https://www.yza.mx/cuidado-personal/higiene-bucal/",
    }


def test_yza_online_location_has_no_postcode_or_store():
    location = next(
        item for item in load_locations()
        if item.id == "yza-online"
    )
    assert location.city == "Catálogo online"
    assert location.state == "Nacional"
    assert location.postal_code is None
    assert location.store is None
    assert location.store_id is None


def test_yza_product_sku_is_from_product_url():
    assert product_sku(
        "https://www.yza.mx/colgate-enjuague-bucal/MXYZ_7891024179925.html"
    ) == "7891024179925"
    assert product_sku("https://www.yza.mx/medicamentos/respiratorio/") is None


def test_yza_offer_price_parser():
    assert prices_from_card(
        "$213.60 (Oferta) Precio reducido de $267.00 (Antes)"
    ) == (213.60, 267.00)
    assert prices_from_card("Sin precio") == (None, None)


def test_yza_pagination_url_is_limited_to_store_domain():
    url = "https://www.yza.mx/cuidado-personal/higiene-bucal/"
    assert page_url(url, 1) == url
    assert page_url(url, 2) == url + "?p=2"
    with pytest.raises(ValueError):
        page_url("https://example.com/other", 2)
    with pytest.raises(ValueError):
        page_url(url, 0)


def test_yza_operator_tab_is_reused_without_closing_chrome():
    page = SimpleNamespace(url="https://www.yza.mx/")
    class Browser:
        contexts = [SimpleNamespace(pages=[page])]
        def close(self):
            raise AssertionError("Must not close operator Chrome")
    class Chromium:
        def launch(self, **kwargs):
            raise AssertionError("Do not launch another Chrome")
        def connect_over_cdp(self, url, **kwargs):
            assert url == "http://127.0.0.1:9222"
            assert kwargs["timeout"] == 60000
            assert kwargs["no_defaults"] is True
            return Browser()
    with yza_browser_page(
        SimpleNamespace(chromium=Chromium()),
        "http://127.0.0.1:9222",
    ) as opened:
        assert opened is page


def test_yza_does_not_open_unrelated_tab_on_cdp():
    class Chromium:
        def connect_over_cdp(self, *args, **kwargs):
            return SimpleNamespace(contexts=[
                SimpleNamespace(pages=[SimpleNamespace(
                    url="https://www.google.com/"
                )])
            ])
    with pytest.raises(YzaContextError, match="pestaña"):
        with yza_browser_page(
            SimpleNamespace(chromium=Chromium()), "http://127.0.0.1:9222"
        ):
            raise AssertionError("Should not yield an unrelated page")


def test_auto_browser_is_still_closed_if_used_directly():
    events = []
    page = SimpleNamespace(url="about:blank")
    class Context:
        def new_page(self):
            return page
        def close(self):
            events.append("close-context")
    class Browser:
        def new_context(self, **kwargs):
            assert kwargs["locale"] == "es-MX"
            return Context()
        def close(self):
            events.append("close-browser")
    class Chromium:
        def launch(self, **kwargs):
            assert kwargs == {"headless": False, "channel": "chrome"}
            return Browser()
    with yza_browser_page(SimpleNamespace(chromium=Chromium())) as found:
        assert found is page
    assert events == ["close-context", "close-browser"]


def test_open_online_category_navigates_without_postal_actions():
    class FakePage:
        url = "https://www.yza.mx/"
        def __init__(self):
            self.urls = []
        def goto(self, url, **kwargs):
            self.urls.append(url)
            self.url = url
        def wait_for_timeout(self, ms):
            pass
        def get_by_placeholder(self, *args, **kwargs):
            raise AssertionError("Must not access postcode input")
        def get_by_role(self, *args, **kwargs):
            raise AssertionError("Must not click postcode buttons")
        def evaluate(self, *args, **kwargs):
            raise AssertionError("Must not verify locality")
    page = FakePage()
    target = "https://www.yza.mx/medicamentos/respiratorio/"
    open_online_category(page, target)
    assert page.urls == [target]
    open_online_category(page, target)
    assert page.urls == [target]


def test_open_online_category_rejects_external_site():
    page = SimpleNamespace(url="https://www.yza.mx/")
    with pytest.raises(YzaScrapeError, match="URL"):
        open_online_category(page, "https://example.com/other")


def test_open_online_category_requires_yza_tab_not_postal_code():
    page = SimpleNamespace(url="https://www.google.com/")
    with pytest.raises(YzaContextError, match="pestaña"):
        open_online_category(
            page, "https://www.yza.mx/cuidado-personal/higiene-bucal/"
        )


def test_full_two_page_capture_succeeds_without_any_postal_code(monkeypatch):
    """Capture every page and preserve NO_VERIFICATION for every product."""
    from scraper.retailers import yza as module
    category = load_categories("config/yza/categories.yaml")[0]
    location = next(loc for loc in load_locations() if loc.id == "yza-online")
    class Page:
        url = "https://www.yza.mx/"
        def goto(self, url, **kwargs):
            self.url = url
        def wait_for_timeout(self, ms):
            pass
        def locator(self, selector):
            assert selector == "body"
            return SimpleNamespace(inner_text=lambda **kw: "Catálogo YZA")

    page = Page()
    @contextmanager
    def fake_page(*args, **kwargs):
        yield page
    @contextmanager
    def fake_playwright():
        yield SimpleNamespace()

    def fake_cards(p):
        second = "p=2" in p.url
        suffix = "222222" if second else "111111"
        return ([{
            "url": f"https://www.yza.mx/item/MXYZ_{suffix}.html",
            "title": f"Producto {suffix}",
            "text": "$50.00",
        }], 2)

    monkeypatch.setattr(module, "yza_browser_page", fake_page)
    monkeypatch.setattr(module, "discover_cards", fake_cards)
    with patch("playwright.sync_api.sync_playwright", fake_playwright):
        scraper = YzaScraper(cdp_url="http://127.0.0.1:9222", max_pages=2)
        rows = scraper.scrape_category(category, location)
    assert scraper.run_meta["status"] == "SUCCESS"
    assert scraper.run_meta["pages_visited"] == 2
    assert scraper.run_meta["postal_verification_required"] is False
    assert len(rows) == 2
    assert len({row["sku"] for row in rows}) == 2
    assert all(row["postal_code"] is None for row in rows)
    assert all(row["store"] is None and row["store_id"] is None for row in rows)
    assert all(row["store_context_verified"] is False for row in rows)
    assert all(row["is_available"] is None for row in rows)


def test_three_attempts_stay_on_same_category_without_location(monkeypatch):
    import scripts.test_yza as runner
    category = load_categories("config/yza/categories.yaml")[0]
    location = next(loc for loc in load_locations() if loc.id == "yza-online")
    calls = []
    class BrokenScraper:
        def __init__(self, cdp_url, max_pages):
            calls.append((cdp_url, max_pages))
        def scrape_category(self, *args, **kwargs):
            raise YzaScrapeError("Paginacion incompleta")
    with patch.object(runner, "YzaScraper", BrokenScraper), \
         patch.object(runner.time, "sleep"):
        frame, summary = runner.run_category(
            category, location, "http://127.0.0.1:9222", 5
        )
    assert len(calls) == 3
    assert frame.empty
    assert summary["status"] == "PARTIAL"
    assert summary["postal_code"] is None
    assert summary["postal_verification_required"] is False


def _online_cached_example():
    from scripts.test_yza import _valid_cached
    day = "2026-10-27"
    category = "higiene-bucal"
    rows = pd.DataFrame([{
        "category_id": category,
        "scrape_timestamp": day + "T09:00:00",
        "sku": "12345",
        "url": "https://www.yza.mx/item/MXYZ_12345.html",
        "price_current": 20,
        "postal_code": None,
        "store": None,
        "store_id": None,
        "store_context_verified": False,
    }])
    summary = {
        "category_id": category, "status": "SUCCESS",
        "capture_date": day, "products": 1,
        "postal_code": None, "postal_verification_required": False,
    }
    return _valid_cached, rows, summary, category, day


def test_online_cache_accepts_current_date_without_postal_code():
    checker, rows, entry, category, day = _online_cached_example()
    assert checker(rows, entry, category, day)
    assert not checker(rows, entry, category, "2026-10-28")


def test_online_cache_rejects_legacy_postal_attribution():
    checker, rows, entry, category, day = _online_cached_example()
    assert not checker(rows.assign(postal_code="11590"), entry, category, day)
    bad = dict(entry, postal_code="11590")
    assert not checker(rows, bad, category, day)


def test_online_cache_rejects_fabricated_location_verification():
    checker, rows, entry, category, day = _online_cached_example()
    assert not checker(rows.assign(store_context_verified=True),
                       entry, category, day)
    assert not checker(rows.assign(store_id="fake"), entry, category, day)
    assert not checker(rows.assign(store="fake"), entry, category, day)


def test_yza_pilot_does_not_change_productive_master():
    runner = Path("scripts/test_yza.py").read_text(encoding="utf-8")
    scraper = Path("scraper/retailers/yza.py").read_text(encoding="utf-8")
    assert "integrate_complete_retailers" not in runner
    assert "MASTER: SIN CAMBIOS" in runner
    assert "yza_online_test.xlsx" in runner
    assert "yza_cp11590_test.xlsx" not in runner
    assert "apply_postal_code" not in scraper
    assert "context_evidence" not in scraper
