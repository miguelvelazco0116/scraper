"""Prevent accidental promotion of Chedraui category candidates to Phase 1."""
from pathlib import Path

from scraper.config import load_categories, load_locations


def test_respiratorios_already_active_and_not_duplicated():
    active = load_categories("config/chedraui/categories.yaml")
    ids = [category.id for category in active]
    assert len(active) == 5
    assert ids.count("respiratorios") == 1
    assert next(category for category in active if category.id == "respiratorios").url == (
        "https://www.chedraui.com.mx/farmacia/medicamentos/respiratorios"
    )


def test_medical_equipment_exact_url_is_cli_candidate_only():
    active_ids = {
        c.id for c in load_categories("config/chedraui/categories.yaml")
    }
    candidate = load_categories(
        "config/chedraui/candidate_categories.yaml"
    )
    assert len(candidate) == 1
    assert candidate[0].id == "equipo-y-botiquin-medico"
    assert candidate[0].department == "Farmacia"
    assert candidate[0].url == (
        "https://www.chedraui.com.mx/farmacia/equipo-y-botiquin-medico"
    )
    assert candidate[0].id not in active_ids
    assert "respiratorios" not in {c.id for c in candidate}


def test_pilot_command_is_restricted_and_separately_stored():
    script = Path("scripts/test_chedraui.py").read_text(encoding="utf-8")
    wrapper = Path("scripts/test_chedraui.ps1").read_text(encoding="utf-8")
    assert '"--pilot"' in script
    assert "args.category in candidate_ids and not args.pilot" in script
    assert 'args.category in {"full", "candidates"}' in script
    assert '"pilots" / "chedraui"' in script
    assert "if status == \"COMPLETE\" and not args.pilot:" in script
    assert "[] if args.pilot else seed_from_prior_test(" in script
    assert '"equipo-y-botiquin-medico"' in wrapper
    assert "[switch]$Pilot" in wrapper
    assert '$ArgsList += "--pilot"' in wrapper


def test_four_stores_remain_productive_unmodified():
    locations = {
        x.id: x for x in load_locations("config/locations.yaml")
    }
    assert locations["chedraui-polanco"].store_id == "232"
    assert locations["chedraui-tepeyac"].store_id == "173"
    assert locations["chedraui-cancun-kabah"].store_id == "255"
    assert locations["chedraui-puebla-centro-sur"].store_id == "76"
