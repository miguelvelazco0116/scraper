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
    apply_postal_code, context_evidence, verify_operator_ready_category,
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
        def connect_over_cdp(self, url, **kwargs):
            assert kwargs.get("timeout") == 60000
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
        def connect_over_cdp(self, url, **kwargs):
            assert kwargs.get("timeout") == 60000
            assert url == "http://127.0.0.1:9222"
            return Browser()
    with yza_browser_page(
        SimpleNamespace(chromium=Chromium()),
        "http://127.0.0.1:9222",
    ) as opened:
        assert opened is page





def test_yza_manual_session_verifies_cp_before_navigating(monkeypatch):
    from scraper.retailers import yza as module

    class PreparedPage:
        url = "https://www.yza.mx/"
        cp = "11590"
        events = []
        def goto(self, url, **kwargs):
            self.events.append("navigate")
            self.url = url
        def wait_for_timeout(self, ms):
            self.events.append("wait")

    page = PreparedPage()
    def visible_header(p):
        p.events.append("verify")
        return "header_cp:11590" if p.cp == "11590" else None
    monkeypatch.setattr(module, "context_evidence", visible_header)
    trace = {}
    category = "https://www.yza.mx/medicamentos/respiratorio/"
    assert verify_operator_ready_category(page, category, trace) == "header_cp:11590"
    assert page.events == ["verify", "navigate", "wait", "verify"]
    assert trace["postal_visible_before_navigation"] is True
    assert trace["postal_visible_after_navigation"] is True
    assert trace["stage"] == "operator_postal_verified"


def test_yza_manual_session_rejects_wrong_cp_without_navigation(monkeypatch):
    from scraper.retailers import yza as module

    class WrongPage:
        url = "https://www.yza.mx/"
        def goto(self, *args, **kwargs):
            raise AssertionError("Must not navigate before CP verification")
    page = WrongPage()
    monkeypatch.setattr(module, "context_evidence", lambda _: None)
    trace = {}
    with pytest.raises(YzaContextError, match="11590"):
        verify_operator_ready_category(
            page, "https://www.yza.mx/medicamentos/respiratorio/", trace
        )
    assert trace["stage"] == "operator_postal_not_verified"


def test_yza_manual_session_rejects_lost_cp_after_navigation(monkeypatch):
    from scraper.retailers import yza as module

    class LostPage:
        url = "https://www.yza.mx/"
        cp = "11590"
        def goto(self, url, **kwargs):
            self.url = url
            self.cp = "44220"
        def wait_for_timeout(self, ms):
            pass

    monkeypatch.setattr(
        module, "context_evidence",
        lambda p: "header_cp:11590" if p.cp == "11590" else None,
    )
    trace = {}
    with pytest.raises(YzaContextError, match="no conserva"):
        verify_operator_ready_category(
            LostPage(), "https://www.yza.mx/cuidado-personal/higiene-bucal/",
            trace,
        )
    assert trace["postal_visible_before_navigation"] is True
    assert trace["postal_visible_after_navigation"] is False
    assert trace["stage"] == "operator_postal_lost_after_navigation"


def test_yza_manual_cdp_requires_preopened_yza_tab():
    class Chromium:
        def connect_over_cdp(self, url, **kwargs):
            assert kwargs.get("timeout") == 60000
            assert url == "http://127.0.0.1:9222"
            return SimpleNamespace(
                contexts=[SimpleNamespace(pages=[
                    SimpleNamespace(url="https://www.google.com/")
                ], new_page=lambda: (_ for _ in ()).throw(
                    AssertionError("Must not open a tab automatically")
                ))]
            )
        def launch(self, **kwargs):
            raise AssertionError("Must connect to Chrome, not launch it")

    with pytest.raises(YzaContextError, match="pestaña"):
        with yza_browser_page(
            SimpleNamespace(chromium=Chromium()),
            "http://127.0.0.1:9222",
        ):
            raise AssertionError("Should not yield an unrelated tab")


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




class FakeYzaPostalLocator:
    def __init__(self, items):
        self.items = items
    def count(self):
        return len(self.items)
    def nth(self, index):
        return self.items[index]


class FakeYzaPostalControl:
    def __init__(self, visible=True, click=None):
        self.visible = visible
        self.click_handler = click
    def is_visible(self):
        return self.visible
    def click(self, timeout=5000):
        if self.click_handler:
            self.click_handler()


class FakeYzaPostalPage:
    def __init__(self, *, popup_open=True, persist=True, old_cp=None):
        self.popup_open = popup_open
        self.persist = persist
        self.old_cp = old_cp
        self.header_cp = old_cp
        self.entered = ""
        self.events = []
        self.field = FakeYzaPostalControl(visible=True)
        self.field.fill = self.fill
        self.field.input_value = self.input_value

    def evaluate(self, js):
        # Proof comes from rendered delivery/header context, NEVER the
        # postal input and never from the value being typed.
        assert "header" in js
        assert '[role="dialog"]' in js
        assert "11590" in js
        return (
            "yza_delivery_postal_confirmed:11590"
            if self.header_cp == "11590" else None
        )

    def get_by_placeholder(self, placeholder):
        assert placeholder.search("Código Postal")
        return FakeYzaPostalLocator(
            [self.field] if self.popup_open else []
        )

    def get_by_role(self, role, name):
        assert role == "button"
        assert name.search("ACEPTAR")
        return FakeYzaPostalLocator([
            FakeYzaPostalControl(click=self.accept)
        ] if self.popup_open else [])

    def get_by_text(self, text, exact=False):
        if text.search("INGRESA TU DIRECCIÓN"):
            return FakeYzaPostalLocator([
                FakeYzaPostalControl(click=self.open_popup)
            ])
        return FakeYzaPostalLocator([])

    def open_popup(self):
        self.events.append("open_modal")
        self.popup_open = True

    def fill(self, postal, timeout=5000):
        self.events.append("fill")
        self.entered = postal

    def input_value(self, timeout=2000):
        return self.entered

    def accept(self):
        self.events.append("accept")
        if self.persist and self.entered == "11590":
            self.header_cp = "11590"
        self.popup_open = False

    def wait_for_timeout(self, milliseconds):
        pass


def test_yza_postal_popup_fills_11590_and_clicks_aceptar():
    page = FakeYzaPostalPage(popup_open=True)
    assert apply_postal_code(page) == "yza_delivery_postal_confirmed:11590"
    assert page.entered == "11590"
    assert page.events == ["fill", "accept"]


def test_yza_clicks_delivery_header_if_postal_popup_is_closed():
    page = FakeYzaPostalPage(popup_open=False)
    assert apply_postal_code(page) == "yza_delivery_postal_confirmed:11590"
    assert page.events == ["open_modal", "fill", "accept"]


def test_yza_postal_input_or_clicked_aceptar_alone_is_not_proof():
    page = FakeYzaPostalPage(popup_open=True, persist=False, old_cp="52164")
    assert apply_postal_code(page) is None
    assert page.entered == "11590"
    assert page.header_cp == "52164"


def test_yza_first_load_popup_is_processed_without_clicking_header():
    """The actual YZA screenshot shows the CP dialog on first navigation."""
    class InitialPopup(FakeYzaPostalPage):
        def get_by_text(self, text, exact=False):
            if text.search("Ingresa un código postal"):
                return FakeYzaPostalLocator([FakeYzaPostalControl()])
            return super().get_by_text(text, exact=exact)

    page = InitialPopup(popup_open=True)
    trace = {}
    result = apply_postal_code(page, trace=trace)
    assert result == "yza_delivery_postal_confirmed:11590"
    assert page.events == ["fill", "accept"]
    assert trace["initial_postal_popup_visible"] is True
    assert trace["accept_clicked"] is True
    assert trace.get("delivery_header_clicked") is None


def test_yza_open_popup_without_input_never_clicks_covered_header():
    """Visible popup with changed DOM must fail closed, not click behind it."""
    class PopupWithoutField(FakeYzaPostalPage):
        def get_by_text(self, text, exact=False):
            if text.search("Ingresa un código postal"):
                return FakeYzaPostalLocator([FakeYzaPostalControl()])
            return super().get_by_text(text, exact=exact)

    page = PopupWithoutField(popup_open=False)
    trace = {}
    assert apply_postal_code(page, trace=trace) is None
    assert page.events == []
    assert trace["initial_postal_popup_visible"] is True
    assert trace["stage"] == "initial_popup_visible_but_controls_missing"
    assert trace.get("delivery_header_clicked") is None


def test_yza_two_step_address_chooser_to_postal_modal():
    """Header can open address chooser; a second click opens postal input."""
    class NestedPostalPage(FakeYzaPostalPage):
        def __init__(self):
            super().__init__(popup_open=False)
            self.address_chooser_open = False

        def open_popup(self):
            self.events.append("open_address_chooser")
            self.address_chooser_open = True

        def get_by_text(self, text, exact=False):
            if text.search("INGRESA TU DIRECCIÓN"):
                return FakeYzaPostalLocator([
                    FakeYzaPostalControl(click=self.open_popup)
                ])
            if (text.search("Ingresa un código postal")
                    and self.address_chooser_open):
                def open_postal():
                    self.events.append("open_postal_modal")
                    self.popup_open = True
                return FakeYzaPostalLocator([
                    FakeYzaPostalControl(click=open_postal)
                ])
            return FakeYzaPostalLocator([])

    page = NestedPostalPage()
    flow = {}
    result = apply_postal_code(page, trace=flow)
    assert result == "yza_delivery_postal_confirmed:11590"
    assert page.events == [
        "open_address_chooser", "open_postal_modal", "fill", "accept"
    ]
    assert flow["postal_subdialog_clicked"] is True
    assert flow["accept_clicked"] is True
    assert flow["stage"] == "applied_postal_confirmed"


def test_yza_cp_trace_distinguishes_click_from_applied_context():
    page = FakeYzaPostalPage(popup_open=True, persist=False, old_cp="52164")
    trace = {}
    assert apply_postal_code(page, trace=trace) is None
    assert trace["field_filled"] is True
    assert trace["accept_clicked"] is True
    assert trace["stage"] == "accept_clicked_but_postal_not_confirmed"
    assert "entered" not in trace
    assert page.header_cp == "52164"


def test_yza_postal_evidence_rejects_non_location_values():
    class StubPage:
        def evaluate(self, js):
            assert "modal-popup" in js
            assert "selected-address" in js
            assert "11590" in js
            assert "document.cookie" not in js
            return None
    assert context_evidence(StubPage()) is None


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
