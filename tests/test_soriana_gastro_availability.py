"""Offline regression tests for Soriana product-tile online availability.

No network, Chrome, scrapers or productive Excel changes.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from scraper.availability import AVAILABLE, UNAVAILABLE, UNKNOWN
from scraper.config import Category, load_categories
from scraper.retailers.soriana import SorianaScraper, PRODUCT_SELECTOR
from scraper.retailers.soriana_availability import classify_soriana_availability
from scripts.test_soriana_gastro import load_pilot_category, summarize
from scripts.test_soriana_manual_phase2 import target_count


def test_gastro_category_registered_but_phase2_remains_six():
    defaults = load_categories("config/soriana/categories.yaml")
    assert len(defaults) == 6
    assert "gastrointestinales" not in {item.id for item in defaults}
    all_categories = load_categories(
        "config/soriana/categories.yaml", include_disabled=True
    )
    assert len(all_categories) == 7
    target = load_pilot_category()
    assert target.url == (
        "https://www.soriana.com/farmacia/gastrointenstinales/"
        "?cgid=gastrointestinales"
    )


def test_available_add_plus_returns_one():
    for caption in ("Agregar +", "AGREGAR", "Agregar al carrito"):
        status, available, raw = classify_soriana_availability([caption])
        assert status == AVAILABLE
        assert available is True
        assert caption in raw


def test_unavailable_agotado_returns_zero():
    for caption in ("Agotado en línea", "AGOTADO EN LINEA"):
        status, available, raw = classify_soriana_availability([caption])
        assert status == UNAVAILABLE
        assert available is False
        assert raw


def test_absence_and_conflicting_controls_remain_unknown():
    assert classify_soriana_availability([])[0:2] == (UNKNOWN, None)
    assert classify_soriana_availability(["Agregar a favoritos"])[0:2] == (UNKNOWN, None)
    assert classify_soriana_availability(
        ["Agregar +", "Agotado en línea"]
    )[0:2] == (UNKNOWN, None)
    assert classify_soriana_availability(
        [], tile_text="Agotado en línea"
    )[0:2] == (UNAVAILABLE, False)
    assert classify_soriana_availability(
        [], tile_text="Pepto Bismol $192"
    )[0:2] == (UNKNOWN, None)


def test_two_tiles_have_independent_product_availability(tmp_path):
    products = [
        {
            "data_pid": "111",
            "product": "Pepto Bismol con 24 Tabletas",
            "url": "/pepto-a/111.html",
            "price_texts": ["$192.00", "$249.00"],
            "promo_texts": ["Puntos dobles"],
            "availability_controls": ["Agotado en línea"],
            "tile_text": "Pepto Bismol con 24 Tabletas Agotado en línea",
        },
        {
            "data_pid": "222",
            "product": "Pepto Bismol Sabor Cereza 24 Tabletas",
            "url": "/pepto-b/222.html",
            "price_texts": ["$146.00"],
            "promo_texts": [],
            "availability_controls": ["Agregar +"],
            "tile_text": "Pepto Bismol Sabor Cereza 24 Tabletas",
        },
        {
            "data_pid": "333",
            "product": "Producto sin botones",
            "url": "/generic/333.html",
            "price_texts": ["$120.00"],
            "promo_texts": [],
            "availability_controls": [],
            "tile_text": "Producto sin botones",
        },
    ]

    class Locator:
        def count(self):
            return len(products)
        def evaluate_all(self, expression):
            assert "availability_controls" in expression
            return products

    class Page:
        def locator(self, selector):
            assert selector == PRODUCT_SELECTOR
            return Locator()

    scraper = SorianaScraper(diagnostics_dir=tmp_path)
    location = SimpleNamespace(city="CDMX", state="CDMX", postal_code=None,
                               store=None, store_id=None)
    category = Category(
        id="gastrointestinales", name="Gastrointestinales",
        url="https://www.soriana.com/farmacia/gastrointenstinales/"
    )
    result = scraper._extract_cards(Page(), category, location)
    assert len(result) == 3
    assert [row["is_available"] for row in result] == [False, True, None]
    assert [row["availability_status"] for row in result] == [
        UNAVAILABLE, AVAILABLE, UNKNOWN
    ]
    assert [row["price_current"] for row in result] == [192, 146, 120]
    assert result[0]["price_regular"] == 249
    assert result[1]["availability_raw"] == "Agregar +"


def test_target_count_reads_resultados_label():
    class Locator:
        def inner_text(self, timeout):
            return "Gastrointenstinales   179 resultados"

    class Page:
        def locator(self, selector):
            assert selector == "body"
            return Locator()

    assert target_count(Page()) == 179


def test_pilot_flags_unknown_or_incomplete_pagination():
    df = pd.DataFrame({
        "availability_status": [AVAILABLE, UNAVAILABLE, UNKNOWN],
        "sku": ["a", "b", "c"],
        "url": ["a.html", "b.html", "c.html"],
        "price_current": [10, 20, 30],
    })
    first = summarize(df, target=3, page_meta={
        "pagination_complete": True, "pages_visited": 2,
        "pages_expected": 2,
    }, attempt=1)
    assert first["status"] == "PARTIAL"
    assert first["available_online"] == 1
    assert first["unavailable_online"] == 1
    assert first["availability_unknown"] == 1
    assert first["availability_complete"] == 2
    assert "disponibilidad online incompleto" in first["quality_notes"]

    second = summarize(
        df.assign(availability_status=[AVAILABLE, UNAVAILABLE, UNAVAILABLE]),
        target=3, page_meta={
            "pagination_complete": False, "pages_visited": 1,
            "pages_expected": 2,
        }, attempt=2,
    )
    assert second["status"] == "PARTIAL"
    assert "paginación incompleta" in second["quality_notes"]


def test_pilot_does_not_change_production_master_or_expected():
    pilot_source = Path("scripts/test_soriana_gastro.py").read_text(
        encoding="utf-8"
    )
    assert 'soriana_manual_phase2_test.xlsx' not in pilot_source
    assert 'concentrado_productivo' not in pilot_source
    assert 'integrate_complete_retailers' not in pilot_source
    assert 'MAX_ATTEMPTS = 3' in pilot_source
