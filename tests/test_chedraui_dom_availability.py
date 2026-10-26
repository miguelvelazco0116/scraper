"""Regression tests for Chedraui buy-control availability, no live scraping."""
from __future__ import annotations

from types import SimpleNamespace

from scraper.availability import AVAILABLE, UNAVAILABLE, UNKNOWN
from scraper.config import Category
from scraper.retailers.chedraui import ChedrauiScraper, PRODUCT_SELECTOR
from scraper.retailers.chedraui_availability import from_chedraui_card_controls
from scraper.retailers.chedraui_polanco_api import ChedrauiScraper as ApiScraper


def control(label, *, disabled=False, visible=True):
    return {"text": label, "disabled": disabled, "visible": visible}


def test_orange_agregar_is_available_only_when_clickable():
    result = from_chedraui_card_controls(
        [control("Agregar")],
        card_text="Gel facial NIVEA $147.00 Agregar Venta por pieza",
    )
    assert result["availability_status"] == AVAILABLE
    assert result["is_available"] is True
    assert result["availability_raw"].startswith("dom_cta:")
    for label in ("Agregar", "Agregar +", "Agregar al carrito"):
        assert from_chedraui_card_controls([control(label)])["is_available"] is True
    assert from_chedraui_card_controls(
        [control("Agregar", disabled=True)]
    )["availability_status"] == UNKNOWN
    assert from_chedraui_card_controls(
        [control("Agregar", visible=False)]
    )["availability_status"] == UNKNOWN


def test_no_unavailable_reference_means_no_fabricated_stockout():
    for labels in ([], [control("Agregar a favoritos")], [control("Agregar dirección")]):
        result = from_chedraui_card_controls(
            labels, card_text="L'Oreal serum $199.00"
        )
        assert result["availability_status"] == UNKNOWN
        assert result["is_available"] is None


def test_only_explicit_product_sold_out_marks_unavailable():
    out = from_chedraui_card_controls(
        [], card_text="Garnier serum Agotado"
    )
    assert out["availability_status"] == UNAVAILABLE
    assert out["is_available"] is False
    delivery = from_chedraui_card_controls(
        [], card_text="Entrega a domicilio no disponible"
    )
    assert delivery["availability_status"] == UNKNOWN


def test_conflicting_same_product_signals_stay_unknown():
    mixed = from_chedraui_card_controls(
        [control("Agregar"), control("Agotado")],
        card_text="Producto $100 Agotado",
    )
    assert mixed["availability_status"] == UNKNOWN
    assert mixed["is_available"] is None
    assert "CONFLICTO" in mixed["availability_raw"]


def test_product_cards_do_not_inherit_buttons_from_neighbors(tmp_path):
    raw_cards = [
        {
            "href": "/gel-facial-1234567/p",
            "product": "Gel facial refrescante Nivea",
            "selling": "$147.00",
            "regular": "$147.00",
            "price_texts": ["$147.00"],
            "promo_texts": [],
            "cta_controls": [control("Agregar")],
            "card_text": "Gel facial Nivea $147 Agregar Venta por pieza",
        },
        {
            "href": "/serum-garnier-7654321/p",
            "product": "Serum Garnier",
            "selling": "$185.00",
            "regular": "$225.00",
            "price_texts": ["$185.00", "$225.00"],
            "promo_texts": [],
            "cta_controls": [],
            "card_text": "Serum Garnier $185 Venta por pieza",
        },
    ]

    class Locator:
        def count(self):
            return 2

        def evaluate_all(self, js):
            assert "cta_controls" in js
            return raw_cards

    class Page:
        def locator(self, selector):
            assert selector == PRODUCT_SELECTOR
            return Locator()

    scraper = ChedrauiScraper(
        require_store_context=False, diagnostics_dir=tmp_path
    )
    scraper._active_store_context_method = "visible_store_control"
    location = SimpleNamespace(
        city="Miguel Hidalgo", state="CDMX", postal_code="11500",
        store="Chedraui Selecto México Polanco", store_id="232"
    )
    category = Category(
        id="cuidado-facial", name="Cuidado facial",
        url="https://www.chedraui.com.mx/cuidado-facial"
    )
    rows = scraper._extract_cards(Page(), category, location)
    assert len(rows) == 2
    assert rows[0]["availability_status"] == AVAILABLE
    assert rows[1]["availability_status"] == UNKNOWN
    assert rows[1]["is_available"] is None
    assert rows[0]["store_id"] == rows[1]["store_id"] == "232"
    assert rows[1]["price_current"] == 185.0


def api_row(sku: str, status: str, value):
    return {
        "url": f"https://www.chedraui.com.mx/producto-{sku}/p",
        "store_id": "232", "category_id": "cuidado-facial",
        "store_context_verified": True,
        "availability_status": status, "is_available": value,
        "availability_raw": "productSearchV3 AvailableQuantity=None"
        if status == UNKNOWN else "productSearchV3 AvailableQuantity=0",
    }


def html_row(sku: str, status: str, value):
    return {
        "url": f"https://www.chedraui.com.mx/producto-{sku}/p",
        "store_id": "232", "category_id": "cuidado-facial",
        "store_context_verified": True,
        "availability_status": status, "is_available": value,
        "availability_raw": "dom_cta: Agregar",
    }


def test_api_explicit_inventory_is_authoritative():
    api = [
        api_row("1", UNAVAILABLE, False),
        api_row("2", AVAILABLE, True),
        api_row("3", UNKNOWN, None),
    ]
    dom = [
        html_row("1", AVAILABLE, True),
        html_row("2", UNAVAILABLE, False),
        html_row("3", AVAILABLE, True),
    ]
    out = ApiScraper._complete_unknown_availability_from_dom(api, dom)
    assert [x["availability_status"] for x in out] == [
        UNAVAILABLE, AVAILABLE, AVAILABLE,
    ]
    assert out[2]["availability_raw"].endswith("dom_cta: Agregar")
    assert api[2]["availability_status"] == UNKNOWN  # no mutation


def test_dom_merging_requires_same_product_category_and_store():
    api = [api_row("100", UNKNOWN, None)]
    dom = [
        html_row("100", AVAILABLE, True) | {"store_id": "173"},
        html_row("200", AVAILABLE, True),
        html_row("100", AVAILABLE, True) | {"category_id": "other"},
    ]
    result = ApiScraper._complete_unknown_availability_from_dom(api, dom)
    assert result[0]["availability_status"] == UNKNOWN


def test_ambiguous_duplicate_dom_products_cannot_certify_availability():
    api = [api_row("100", UNKNOWN, None)]
    dom = [
        html_row("100", AVAILABLE, True),
        html_row("100", UNAVAILABLE, False),
    ]
    result = ApiScraper._complete_unknown_availability_from_dom(api, dom)
    assert result[0]["availability_status"] == UNKNOWN


def test_normal_price_quality_stays_required_for_unknown_stock():
    from scripts.test_chedraui import _quality
    import pandas as pd

    frame = pd.DataFrame([{
        "availability_status": UNKNOWN,
        "price_current": None, "price_regular": None,
        "sku": "100", "url": "https://www.chedraui.com.mx/100/p",
    }])
    status, metrics = _quality(frame, {
        "displayed_category_products": 1,
        "store_context_verified": True,
    })
    assert status == "PARTIAL"
    assert metrics["price_required"] == 1
