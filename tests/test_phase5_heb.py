from pathlib import Path

from scraper.config import load_categories
from scraper.quality_rules import meets_rate


def test_heb_category_is_full_higiene_dental():
    categories = {
        item.id: item
        for item in load_categories("config/heb/categories.yaml")
    }
    category = categories["higiene-dental"]
    assert category.department == "Cuidado personal y belleza"
    assert category.name == "Higiene dental"
    assert category.subcategory is None
    assert category.url == (
        "https://www.heb.com.mx/"
        "cuidado-personal-y-belleza/higiene-dental"
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


def test_heb_supports_dom_fallback_when_graphql_is_absent():
    source = Path("scraper/retailers/heb.py").read_text(
        encoding="utf-8"
    )
    assert "def _target_from_body" in source
    assert "def _dom_product_cards" in source
    assert "dom_fallback" in source
    assert r"\s+resultados" in source


def test_heb_runner_accepts_dom_or_structured_transport():
    source = Path("scripts/test_heb.py").read_text(
        encoding="utf-8"
    )
    assert '"dom_fallback"' in source
    assert '"productSearchV3+dom_fallback"' in source


def test_heb_existing_session_uses_native_show_more_pagination():
    source = Path("scraper/retailers/heb.py").read_text(
        encoding="utf-8"
    )
    assert "def scrape_category_on_page" in source
    assert '"pagination_method": (' in source
    assert '"show_more"' in source
    assert '"show_more_clicks": clicks' in source
    assert '"pages_visited": len(pagination_pages)' in source


def test_heb_phase5_uses_existing_chrome_cdp():
    runner = Path("scripts/test_heb.py").read_text(
        encoding="utf-8"
    )
    wrapper = Path("scripts/test_heb.ps1").read_text(
        encoding="utf-8"
    )
    assert "connect_over_cdp" in runner
    assert "--cdp-url" in runner
    assert "DevToolsActivePort" in wrapper
    assert "SESION EXISTENTE" in wrapper


def test_heb_blocked_session_is_not_automatically_retried():
    runner = Path("scripts/test_heb.py").read_text(
        encoding="utf-8"
    )
    assert '!= "BLOCKED"' in runner
    assert "No se harán más reintentos." in runner


def test_heb_existing_session_uses_native_show_more():
    source = Path("scraper/retailers/heb.py").read_text(
        encoding="utf-8"
    )
    assert "def scrape_category_on_page" in source
    assert '"show_more"' in source
    assert 'text="Mostrar más"' in source


def test_heb_manual_mode_navigates_same_existing_tab():
    source = Path("scraper/retailers/heb.py").read_text(
        encoding="utf-8"
    )
    manual = source.split("def scrape_category_on_page", 1)[1].split(
        "def scrape_category(", 1
    )[0]
    assert "window.location.assign(url)" in manual
    assert "page.goto(" not in "\n".join(
        line.split("#", 1)[0]
        for line in manual.splitlines()
    )
    assert "manual_heb_page_same_tab_navigation" in manual


def test_heb_runner_accepts_manual_homepage_and_navigates_same_tab():
    source = Path("scripts/test_heb.py").read_text(
        encoding="utf-8"
    )
    assert "return candidates[-1]" in source
    assert "el scraper navegará desde" in source


def test_heb_manual_pagination_supports_native_next_and_scroll():
    source = Path("scraper/retailers/heb.py").read_text(
        encoding="utf-8"
    )
    assert "def _click_native_next_page" in source
    assert "def _scroll_for_more_products" in source
    assert '"native_next"' in source
    assert '"scroll_lazy_load"' in source
    assert '"native_next_clicks": native_next_clicks' in source
    assert '"scroll_rounds": scroll_rounds' in source


def test_heb_runner_surfaces_native_pagination_diagnostics():
    source = Path("scripts/test_heb.py").read_text(
        encoding="utf-8"
    )
    assert '"native_next_clicks"' in source
    assert '"scroll_rounds"' in source


def test_heb_prefers_visible_numeric_pagination():
    source = Path("scraper/retailers/heb.py").read_text(
        encoding="utf-8"
    )
    assert "def _click_numeric_page" in source
    assert '"method": "numeric_page"' in source
    assert '"numeric_page_clicks": numeric_page_clicks' in source
    assert '"pagination_method": (' in source


def test_heb_runner_surfaces_numeric_page_clicks():
    source = Path("scripts/test_heb.py").read_text(
        encoding="utf-8"
    )
    assert '"numeric_page_clicks"' in source
