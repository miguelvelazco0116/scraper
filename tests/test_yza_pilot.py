"""Offline tests for isolated Farmacias YZA / CP 11590 pilot.

No browser, site requests or productive master writes.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from scraper.config import load_categories, load_locations
from scraper.retailers.yza import (
    POSTAL_CODE, YzaContextError, YzaScraper,
    page_url, prices_from_card, product_sku, yza_browser_page,
)


def test_yza_has_exactly_two_requested_categories():
    categories = load_categories("config/yza/categories.yaml")
    assert {c.id: c.url for c in categories} == {
        "medicamentos-respiratorio":
            "https://www.yza.mx/medicamentos/respiratorio/",
        "higiene-bucal":
            "https://www.yza.mx/cuidado-personal/higiene-bucal/",
    }


def test_fixed_postal_code_is_configured_without_fabricated_store():
    location = next(
        item for item in load_locations()
        if item.id == "yza-cp-11590"
    )
    assert POSTAL_CODE == location.postal_code == "11590"
    assert location.store_id is None


def test_real_yza_product_url_yields_sku():
    assert product_sku(
        "https://www.yza.mx/colgate-enjuague-bucal-periogard-250ml/"
        "MXYZ_7891024179925.html"
    ) == "7891024179925"
    assert product_sku("https://www.yza.mx/medicamentos/respiratorio/") is None


def test_yza_offer_price_not_confused_with_old_price():
    assert prices_from_card(
        "$213.60 (Oferta) Precio reducido de $267.00 (Antes)"
    ) == (213.60, 267.00)
    assert prices_from_card("Sin precio") == (None, None)


def test_page_url_is_restricted_to_yza_and_positive_pages():
    url = "https://www.yza.mx/cuidado-personal/higiene-bucal/"
    assert page_url(url, 2) == url + "?p=2"
    assert page_url(url, 1) == url
    with pytest.raises(ValueError):
        page_url("https://untrusted.example/other/", 2)
    with pytest.raises(ValueError):
        page_url(url, 0)


def test_wrong_cp_fails_before_browser_connection():
    category = load_categories("config/yza/categories.yaml")[0]
    location = SimpleNamespace(postal_code="11500")
    scraper = YzaScraper("ws://127.0.0.1:9222")
    with pytest.raises(YzaContextError, match="11590"):
        scraper.scrape_category(category, location)


def test_three_attempts_on_same_category_before_advancing():
    import scripts.test_yza as runner
    category = load_categories("config/yza/categories.yaml")[0]
    location = SimpleNamespace(postal_code="11590")
    failures = []

    class BrokenYzaScraper:
        def __init__(self, cdp_url, max_pages):
            failures.append(cdp_url)

        def scrape_category(self, category, location):
            raise YzaContextError("El CP no fue aplicado")

    with patch.object(runner, "YzaScraper", BrokenYzaScraper), \
         patch.object(runner.time, "sleep"):
        frame, summary = runner.run_category(
            category, location, "ws://127.0.0.1:9222", 5
        )
    assert len(failures) == 3
    assert frame.empty
    assert summary["status"] == "STORE_CONTEXT_ERROR"



def test_yza_auto_browser_launches_headed_and_cleans_up():
    log = []
    page = SimpleNamespace(url="about:blank")
    class Context:
        def new_page(self):
            log.append("new_page")
            return page
        def close(self):
            log.append("close_context")
    class Browser:
        def new_context(self, **kwargs):
            assert kwargs["locale"] == "es-MX"
            log.append("new_context")
            return Context()
        def close(self):
            log.append("close_browser")
    class Chromium:
        def launch(self, **kwargs):
            assert kwargs == {"headless": False, "channel": "chrome"}
            log.append("launch")
            return Browser()
        def connect_over_cdp(self, url):
            raise AssertionError("Automatic Chrome must not use CDP")
    playwright = SimpleNamespace(chromium=Chromium())
    with yza_browser_page(playwright) as opened:
        assert opened is page
    assert log == [
        "launch", "new_context", "new_page",
        "close_context", "close_browser",
    ]


def test_yza_manual_cdp_reuses_tab_without_closing_operators_browser():
    page = SimpleNamespace(url="https://www.yza.mx/")
    class Browser:
        contexts = [SimpleNamespace(pages=[page])]
        def close(self):
            raise AssertionError("Cannot close operator Chrome")
    class Chromium:
        def launch(self, **kwargs):
            raise AssertionError("CDP fallback must not launch Chrome")
        def connect_over_cdp(self, url):
            assert url == "http://127.0.0.1:9222"
            return Browser()
    with yza_browser_page(
        SimpleNamespace(chromium=Chromium()),
        "http://127.0.0.1:9222",
    ) as opened:
        assert opened is page




def test_yza_postal_probe_metadata_excludes_typed_addresses():
    """The browser can inspect the modal without exposing address contents."""
    from scraper.retailers.yza_postal_diagnostics import inspect_postal_ui

    class FakePage:
        def evaluate(self, js):
            assert "input_fields" in js
            assert "location_controls" in js
            assert "el.value" not in js
            assert "document.cookie" not in js
            assert "localStorage" not in js
            return {
                "input_fields": [{
                    "placeholder": (
                        "Digita la dirección y selecciona una opción "
                        "de la lista de sugerencias"
                    ),
                    "visible": False,
                    "in_dialog": True,
                    "aria_label": "",
                    "type": "text",
                }],
                "location_controls": [
                    {"text": "Consultar disponibilidad", "visible": False,
                     "in_dialog": True}
                ],
                "modal_count": 1,
                "url": "https://www.yza.mx/medicamentos/respiratorio/",
            }

    data = inspect_postal_ui(FakePage())
    assert data["input_fields"][0]["visible"] is False
    assert data["input_fields"][0]["in_dialog"] is True
    assert data["location_controls"][0]["text"] == "Consultar disponibilidad"



def test_valid_cached_rejects_wrong_date_or_cp():
    from scripts.test_yza import _valid_cached

    rows = pd.DataFrame([{
        "category_id": "higiene-bucal", "sku": "12345",
        "url": "https://www.yza.mx/sku/MXYZ_12345.html",
        "price_current": 20,
        "scrape_timestamp": "2026-10-26T08:00:00",
        "postal_code": "11590", "store_context_verified": True,
    }])
    meta = {
        "category_id": "higiene-bucal", "status": "SUCCESS",
        "capture_date": "2026-10-26",
        "postal_code": "11590", "products": 1,
    }
    assert _valid_cached(rows, meta, "higiene-bucal", "2026-10-26")
    assert not _valid_cached(rows, meta, "higiene-bucal", "2026-10-27")
    assert not _valid_cached(
        rows.assign(postal_code="11500"), meta,
        "higiene-bucal", "2026-10-26"
    )


def test_pilot_has_no_master_integration_call():
    runner = Path("scripts/test_yza.py").read_text(encoding="utf-8")
    assert "integrate_complete_retailers" not in runner
    assert "MASTER: SIN CAMBIOS" in runner
