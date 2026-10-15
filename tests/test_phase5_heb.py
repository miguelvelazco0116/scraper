from pathlib import Path

from scraper.config import load_categories
from scraper.quality_rules import meets_rate


def test_heb_category_is_pasta_dental():
    categories = {
        item.id: item
        for item in load_categories("config/heb/categories.yaml")
    }
    category = categories["pasta-dental"]
    assert category.department == "Cuidado personal y belleza"
    assert category.name == "Higiene dental"
    assert category.subcategory == "Pasta dental"
    assert category.url == (
        "https://www.heb.com.mx/"
        "cuidado-personal-y-belleza/higiene-dental/pasta-dental"
    )


def test_heb_scraper_uses_structured_vtex_search():
    source = Path("scraper/retailers/heb.py").read_text(
        encoding="utf-8"
    )
    assert "operationName=productSearchV3" in source
    assert "AvailableQuantity" in source
    assert "vtex-search-result-3-x-buttonShowMore" in source


def test_heb_phase5_uses_90_percent_threshold():
    assert meets_rate(9, 10)
    assert not meets_rate(8, 10)


def test_heb_runner_reuses_same_day_successes():
    source = Path("scripts/test_heb.py").read_text(
        encoding="utf-8"
    )
    assert "def _load_existing_valid_results" in source
    assert "CATEGORÍAS HEB YA VÁLIDAS HOY" in source
    assert "--retry-failed-at-end" in source


def test_heb_availability_is_binary():
    source = Path("scraper/retailers/heb.py").read_text(
        encoding="utf-8"
    )
    assert "is_available = 1" in source
    assert "is_available = 0" in source


def test_phase5_integrator_supports_heb_retailer():
    source = Path("scripts/integrate_validated_phase.py").read_text(
        encoding="utf-8"
    )
    assert '"HEB": {' in source
    assert '"source": ROOT / "output" / "heb_phase5_test.xlsx"' in source
    assert "def _validate_heb_phase" in source
