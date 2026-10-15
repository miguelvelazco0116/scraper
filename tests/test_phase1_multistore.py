from pathlib import Path

from scraper.config import load_categories, load_locations


def test_phase1_chedraui_has_five_productive_categories():
    categories = load_categories("config/chedraui/categories.yaml")
    assert {item.id for item in categories} == {
        "higiene-bucal",
        "lavanderia",
        "desodorantes-y-antitranspirantes",
        "depilacion",
        "respiratorios",
    }


def test_phase1_chedraui_has_four_validated_stores():
    locations = {
        item.id: item
        for item in load_locations("config/locations.yaml")
    }
    ids = (
        "chedraui-polanco",
        "chedraui-tepeyac",
        "chedraui-cancun-kabah",
        "chedraui-puebla-centro-sur",
    )
    assert [locations[item].store_id for item in ids] == [
        "232",
        "173",
        "255",
        "76",
    ]


def test_phase1_runner_declares_multi_store_chedraui():
    source = Path("scripts/run_phase1_production.py").read_text(
        encoding="utf-8"
    )
    for location_id in (
        "chedraui-polanco",
        "chedraui-tepeyac",
        "chedraui-cancun-kabah",
        "chedraui-puebla-centro-sur",
    ):
        assert f'locations["{location_id}"]' in source
    assert "expected_retailer_categories" in source
    assert "expected_cases" in source


def test_phase1_integrator_expects_15_categories_30_cases():
    source = Path("scripts/integrate_validated_phase.py").read_text(
        encoding="utf-8"
    )
    phase1 = source.split("1: {", 1)[1].split("2: {", 1)[0]
    assert '"categories": 15' in phase1
    assert '"cases": 30' in phase1
