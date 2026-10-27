"""Offline Farmatodo pilot tests: no network or productive file writes."""
from pathlib import Path

import pandas as pd

from scraper.config import load_categories
from scraper.retailers.farmatodo import (
    FarmatodoScraper, prices_from_card, sku_from_card,
    explicit_online_availability, FarmatodoCaptureError
)
from scraper.availability import AVAILABLE, UNKNOWN
from scripts.test_farmatodo import quality, valid_cached, MAX_ATTEMPTS


def test_farmatodo_three_requested_urls_exactly():
    categories = load_categories("config/farmatodo/categories.yaml")
    assert {x.id: x.url for x in categories} == {
        "respiratorios": "https://www.farmatodo.com.mx/medicamentos/respiratorios",
        "cuidado-bucal": "https://www.farmatodo.com.mx/cuidado-personal-y-belleza/cuidado-bucal",
        "higiene-personal": "https://www.farmatodo.com.mx/cuidado-personal-y-belleza/higiene-personal",
    }


def test_farmatodo_offer_prices_and_identifier():
    assert prices_from_card("De $145.00 Para $108.50") == (108.5, 145.0)
    assert prices_from_card("$29.50") == (29.5, 29.5)
    assert prices_from_card("Sin precio") == (None, None)
    sku, source = sku_from_card({
        "url": "https://www.farmatodo.com.mx/colgate-xyz/p",
        "image_url": "https://cdn.example/7509546653518_01.webp",
    })
    assert sku == "7509546653518"
    assert source == "image_ean_candidate"
    assert sku_from_card({"url": "https://www.farmatodo.com.mx/no-id/p"}) == (
        None, "not_found",
    )



def test_image_ean_suffixes_and_invalid_embedded_codes():
    for image in (
        "https://cdn.example/7509546653518_01.webp",
        "https://cdn.example/7509546653518-01.webp",
        "https://cdn.example/7509546653518.webp",
        "https://cdn.example/products/7509546653518_02?width=400",
    ):
        assert sku_from_card({"image_url": image}) == (
            "7509546653518", "image_ean_candidate"
        )
    # Never accept a substring of a longer code or mixed alphanumeric ID.
    for image in (
        "https://cdn.example/175095466535189.webp",
        "https://cdn.example/X7509546653518_01.webp",
        "https://cdn.example/7509546653518X.webp",
    ):
        assert sku_from_card({"image_url": image}) == (
            None, "not_found"
        )



def test_online_availability_never_inferred_from_price():
    assert explicit_online_availability("Producto $120")[0:2] == (
        UNKNOWN, None
    )
    assert explicit_online_availability("10 disponibles")[0:2] == (
        AVAILABLE, True
    )


def test_farmatodo_quality_requires_coverage_and_sku():
    data = [{
        "url": "https://www.farmatodo.com.mx/item/p",
        "sku": "7509546653518",
        "price_current": 108.5,
        "postal_code": None,
        "store_context_verified": False,
    }]
    metadata = {
        "category_id": "cuidado-bucal",
        "capture_date": "2026-10-26",
        "target_products": 1,
        "load_more_stalled": False,
        "load_more_remaining": False,
        "sku_source_counts": {"image_ean_candidate": 1},
    }
    ok = quality(data, metadata)
    assert ok["status"] == "COMPLETE"
    assert ok["store_context_verified"] is False
    assert ok["postal_code"] is None
    assert quality(data, metadata | {"target_products": 3})["status"] == "PARTIAL"
    assert quality(
        [{**data[0], "sku": None}], metadata
    )["status"] == "PARTIAL"
    assert quality(
        [{**data[0], "postal_code": "11590"}], metadata
    )["status"] == "PARTIAL"


def test_only_same_day_locationless_complete_can_be_reused():
    df = pd.DataFrame([{
        "category_id": "cuidado-bucal",
        "scrape_timestamp": "2026-10-26T10:00:00-06:00",
        "sku": "7509546653518", "price_current": 108.5,
        "url": "https://www.farmatodo.com.mx/colgate/p",
        "postal_code": None, "store_id": None,
        "store_context_verified": False,
    }])
    summary = {
        "capture_date": "2026-10-26",
        "category_id": "cuidado-bucal", "status": "COMPLETE", "products": 1
    }
    assert valid_cached(df, summary, "2026-10-26") is True
    assert valid_cached(df, summary, "2026-10-27") is False
    assert valid_cached(
        df.assign(postal_code="11590"), summary, "2026-10-26"
    ) is False


def test_pilot_does_not_write_productive_master_or_ask_for_postcode():
    text = Path("scripts/test_farmatodo.py").read_text(encoding="utf-8")
    assert "MAX_ATTEMPTS = 3" in text
    assert "integrate_complete_retailers" not in text
    assert "current_master.json" not in text
    assert "postal_code" in text
    assert "pilots" in text
