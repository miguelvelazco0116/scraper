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
