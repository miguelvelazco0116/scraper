from pathlib import Path

from scraper.quality_rules import meets_rate


def test_similares_phase5_uses_90_percent_threshold():
    assert meets_rate(9, 10)
    assert not meets_rate(8, 10)


def test_similares_phase5_supports_resume_and_selective_retry():
    source = Path("scripts/test_farmacias_similares.py").read_text(
        encoding="utf-8"
    )
    assert "def _load_existing_valid_results" in source
    assert "CATEGORÍAS SIMILARES YA VÁLIDAS HOY" in source
    assert "--retry-failed-at-end" in source
    assert "Sólo se reintentan:" in source


def test_similares_phase5_wrapper_supports_category():
    source = Path("scripts/test_farmacias_similares.ps1").read_text(
        encoding="utf-8"
    )
    assert '[string]$Category = "all"' in source
    assert "--category $Category" in source


def test_similares_discovery_hydrates_without_brittle_selector_gate():
    source = Path("scraper/retailers/farmacias_similares.py").read_text(
        encoding="utf-8"
    )
    assert "def _hydrate_product_links" in source
    assert "discovery_coverage >= 0.90" in source
    assert "wait_for_selector('a[href$=\"/p\"]" not in source


def test_phase5_integrator_uses_four_validated_retailers_and_13_categories():
    # Phase 5 expanded from Similares+HEB (4 categories) to include
    # Sam's Club (3) and Mayorista Duero (6). Keep the test aligned with
    # the production integrator; NEVER reduce the integrator to satisfy
    # a stale four-category assertion.
    from scripts.integrate_validated_phase import (
        PHASES, PHASE5_RETAILER_SOURCES,
    )

    expected = {
        "Farmacias Similares": 2,
        "HEB": 2,
        "Sam's Club": 3,
        "Mayorista Duero": 6,
    }
    assert PHASES[5]["retailers"] == set(expected)
    assert "La Comer" not in PHASES[5]["retailers"]
    assert {retailer: data["categories"]
            for retailer, data in PHASE5_RETAILER_SOURCES.items()} == expected
    assert PHASES[5]["categories"] == sum(expected.values()) == 13

    source = Path("scripts/integrate_validated_phase.py").read_text(
        encoding="utf-8"
    )
    for validator in (
        "_validate_similares_phase", "_validate_heb_phase",
        "_validate_sams_retailer", "_validate_duero_retailer",
    ):
        assert f"def {validator}(" in source


def test_phase5_requires_previous_phases_and_wrapper_accepts_5():
    integrator = Path(
        "scripts/integrate_validated_phase.py"
    ).read_text(encoding="utf-8")
    wrapper = Path(
        "scripts/integrate_validated_phase.ps1"
    ).read_text(encoding="utf-8")
    assert "required_previous = list(range(1, phase))" in integrator
    assert "[ValidateSet(1, 2, 3, 4, 5)]" in wrapper
    assert '"HEB"' in wrapper
    assert '"Farmacias Similares"' in wrapper
