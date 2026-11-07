"""Phase 3: Walmart deodorants are a full, retailer-specific category."""
from pathlib import Path

import pandas as pd

from scraper.config import load_categories
from scripts import run_phase3_production as production
from scripts import integrate_complete_retailers as incremental
from scripts import integrate_validated_phase as validated
from scripts import build_phase3_from_validated_components as components

ROOT = Path(__file__).resolve().parents[1]
URL = (
    "https://www.walmart.com.mx/browse/cuidado-personal/"
    "cuidado-corporal/desodorantes/264479_950004_264503"
)


def test_walmart_deodorants_url_and_category_independent_of_bodega():
    walmart = {x.id: x for x in load_categories(ROOT / "config/walmart/categories.yaml")}
    bodega = {x.id: x for x in load_categories(ROOT / "config/bodega-aurrera/categories.yaml")}
    assert len(walmart) == 4
    assert set(walmart) == {
        "cuidado-bucal", "cuidado-de-la-ropa",
        "depilacion-y-rasurado", "desodorantes",
    }
    assert walmart["desodorantes"].url == URL
    assert walmart["desodorantes"].source_urls == (URL,)
    assert bodega["desodorantes"].url.startswith("https://www.bodegaaurrera.com.mx/")
    assert set(bodega) == {"cuidado-bucal", "cuidado-de-la-ropa", "desodorantes"}


def test_all_phase3_validation_and_integration_contracts_are_seven_cases():
    assert production.EXPECTED_BODEGA_CATEGORIES == 3
    assert production.EXPECTED_WALMART_CATEGORIES == 4
    assert incremental.EXPECTED[3] == {"Bodega Aurrera": 3, "Walmart": 4}
    assert validated.PHASE3_RETAILER_SOURCES["Walmart"]["categories"] == 4
    assert validated.PHASE3_RETAILER_SOURCES["Bodega Aurrera"]["categories"] == 3
    assert validated.PHASES[3]["categories"] == 7
    assert len(components.EXPECTED) == 7
    assert ("Walmart", "desodorantes") in components.EXPECTED
    assert ("Bodega Aurrera", "desodorantes") in components.EXPECTED


def test_incomplete_old_three_category_walmart_cannot_be_certified_as_new_complete():
    frame = pd.DataFrame({"retailer": ["Walmart"] * 3,
                          "category_id": ["cuidado-bucal", "cuidado-de-la-ropa",
                                          "depilacion-y-rasurado"]})
    summary = pd.DataFrame({"retailer": ["Walmart"] * 3,
                            "category_id": frame["category_id"],
                            "status": ["COMPLETE"] * 3,
                            "products": [1, 1, 1]})
    control = pd.DataFrame({"status": ["PRODUCTIVE"]})
    notes = validated._validate_walmart_retailer(frame, summary, control)
    assert "Walmart categorías 3/4" in notes
    production_notes = production._validate_walmart(frame, summary, control)
    assert "Walmart categorías 3/4" in production_notes
