"""Offline Soriana postal rotation regression tests. No Chrome/network."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from scraper.retailers.soriana_postal_context import (
    SorianaPostalContextError,
    attempt_postal_selection, check_postal_code,
    require_postal_context, visible_postal_evidence,
)
from scripts.test_soriana_cp_rotation import (
    configured_postcodes, selected_categories, classify, _valid_cache
)


def test_four_codes_preserve_user_order():
    assert configured_postcodes() == ["83420", "44220", "54030", "97245"]
    for postal in configured_postcodes():
        assert check_postal_code(postal) == postal
    with pytest.raises(ValueError):
        check_postal_code("8342")
    with pytest.raises(ValueError):
        check_postal_code("11A90")


def test_six_productive_categories_remain_unaltered():
    assert len(selected_categories("all")) == 6
    assert selected_categories("gastrointestinales")[0].id == "gastrointestinales"


def test_input_value_is_not_proof_of_postcode():
    class FakePage:
        def evaluate(self, script):
            assert "input,textarea,select" in script
            assert "dialog" in script
            return []
        def locator(self, selector):
            return SimpleNamespace(first=SimpleNamespace(count=lambda: 0))
        def get_by_text(self, value, exact):
            return SimpleNamespace(count=lambda: 0)

    page = FakePage()
    assert visible_postal_evidence(page, "83420") is None
    assert attempt_postal_selection(page, "83420") is None
    with pytest.raises(SorianaPostalContextError):
        require_postal_context(page, "83420")


def test_context_verified_after_persistent_visible_selection():
    class FakePage:
        def evaluate(self, script):
            return [{"value": "97245", "selector": "span", "x": 275, "y": 45}]
    result = require_postal_context(FakePage(), "97245")
    assert "97245" in result


def test_other_zip_in_header_is_not_considered_verified():
    class Header:
        def evaluate(self, script):
            # This is the 52164 from the user's second screenshot,
            # not the requested 83420.
            return [{"value": "52164", "x": 270, "y": 44}]
    assert visible_postal_evidence(Header(), "83420") is None
    with pytest.raises(SorianaPostalContextError, match="52164"):
        require_postal_context(Header(), "83420")


def test_modal_search_replaces_old_cp_and_checks_header():
    class FakeField:
        def __init__(self):
            self.value = "03100"
        def input_value(self, timeout=1500):
            return self.value
        def get_attribute(self, name):
            return None
        def fill(self, value, timeout=5000):
            self.value = value

    class FakeInputSet:
        def __init__(self, field):
            self.field = field
        def count(self):
            return 1
        def nth(self, index):
            return self.field

    class FakeRow:
        def __init__(self, field):
            self.field = field
        def count(self):
            return 1
        def locator(self, selector):
            assert selector == "input:visible"
            return FakeInputSet(self.field)

    class FakeButton:
        def __init__(self, page):
            self.page = page
        def count(self):
            return 1
        def nth(self, i):
            return self
        def is_visible(self):
            return True
        def locator(self, selector):
            assert selector.startswith("xpath=ancestor::")
            return FakeRow(self.page.field)
        def click(self, timeout=6000):
            assert self.page.field.value == "83420"
            self.page.header = "83420"

    class FakePage:
        def __init__(self):
            self.field = FakeField()
            self.header = "52164"
            self.button = FakeButton(self)
        def evaluate(self, script):
            return [{"value": self.header, "x": 270, "y": 45}]
        def get_by_text(self, pattern, exact=None):
            return self.button
        def wait_for_timeout(self, ms):
            return None

    page = FakePage()
    assert attempt_postal_selection(page, "83420") == "header_delivery_cp:83420"
    assert page.field.value == "83420"


def test_unverified_cp_never_passes_capture_quality():
    rows = [{
        "sku": "sku-1", "url": "https://www.soriana.com/item-1.html",
        "price_current": 145.0, "category_id": "cuidado-bucal",
        "postal_code": "83420", "store_id": None,
        "availability_status": "AVAILABLE",
    }]
    page_meta = {
        "pagination_complete": True, "pages_visited": 1,
        "pages_expected": 1,
    }
    no_proof = classify(
        rows, cp="83420", category_id="cuidado-bucal",
        target=1, page_meta=page_meta, proof=None, error=None,
        attempts=1,
    )
    assert no_proof["status"] == "PARTIAL"
    yes_proof = classify(
        rows, cp="83420", category_id="cuidado-bucal",
        target=1, page_meta=page_meta,
        proof="header :: CP 83420", error=None, attempts=1,
    )
    assert yes_proof["status"] == "COMPLETE"
    assert yes_proof["postal_context_verified"] is True
    assert yes_proof["store_context_verified"] is False
    assert yes_proof["available_online"] == 1


def test_same_day_cp_cache_never_crosses_regions():
    frame = pd.DataFrame([{
        "category_id": "cuidado-bucal",
        "postal_code": "83420",
        "scrape_timestamp": "2026-10-26T09:00:00",
    }])
    summary = {
        "category_id": "cuidado-bucal", "postal_code": "83420",
        "capture_date": "2026-10-26",
        "status": "COMPLETE", "postal_context_verified": True,
        "postal_context_evidence": "header: 83420",
        "products": 1,
    }
    assert _valid_cache(
        frame, summary, "83420", "cuidado-bucal", "2026-10-26"
    )
    assert not _valid_cache(
        frame, summary, "44220", "cuidado-bucal", "2026-10-26"
    )
    assert not _valid_cache(
        frame, summary, "83420", "cuidado-bucal", "2026-10-27"
    )


def test_pilot_avoids_productive_phase2_files():
    script = Path("scripts/test_soriana_cp_rotation.py").read_text(encoding="utf-8")
    assert 'soriana_manual_phase2_test.xlsx' not in script
    assert 'current_master.json' not in script
    assert '"pilots" / "soriana_cp_rotation"' in script
    assert "MAX_ATTEMPTS = 3" in script
    assert "page_validator" in script
