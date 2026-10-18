import base64
import json
from urllib.parse import urlencode

from scraper.config import Category
from scraper.retailers.chedraui_polanco_api import ChedrauiScraper


def _product_search_url(variables: dict) -> str:
    encoded = base64.b64encode(
        json.dumps(
            variables,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).decode("ascii")
    extensions = {
        "persistedQuery": {
            "version": 1,
            "sha256Hash": "test",
        },
        "variables": encoded,
    }
    return (
        "https://www.chedraui.com.mx/_v/segment/graphql/v1?"
        + urlencode(
            {
                "operationName": "productSearchV3",
                "extensions": json.dumps(
                    extensions,
                    separators=(",", ":"),
                ),
            }
        )
    )


def _category() -> Category:
    return Category(
        id="desodorantes-y-antitranspirantes",
        name="Cuidado corporal",
        department="Cuidado e higiene personal",
        subcategory="Desodorantes y antitranspirantes",
        url=(
            "https://www.chedraui.com.mx/"
            "cuidado-e-higiene-personal/cuidado-corporal/"
            "desodorantes-y-antitranspirantes"
        ),
    )


def test_category_product_search_beats_late_single_product_auxiliary_search():
    scraper = ChedrauiScraper(require_store_context=False)
    scraper._product_search_category = _category()

    category_url = _product_search_url(
        {
            "from": 0,
            "to": 19,
            "selectedFacets": [
                {
                    "key": "c",
                    "value": "desodorantes-y-antitranspirantes",
                }
            ],
        }
    )
    auxiliary_url = _product_search_url(
        {
            "from": 0,
            "to": 0,
            "fullText": "producto-recomendado",
        }
    )

    scraper._consider_product_search_template(
        category_url,
        records_filtered=378,
        product_count=20,
        source="response",
    )
    scraper._consider_product_search_template(
        auxiliary_url,
        records_filtered=1,
        product_count=1,
        source="response",
    )

    assert scraper._product_search_template_url == category_url
    assert scraper.run_meta["product_search_template"][
        "records_filtered"
    ] == 378


def test_category_affinity_wins_even_if_auxiliary_search_reports_more_records():
    scraper = ChedrauiScraper(require_store_context=False)
    scraper._product_search_category = _category()

    category_url = _product_search_url(
        {
            "from": 0,
            "to": 19,
            "selectedFacets": [
                {
                    "key": "c",
                    "value": "desodorantes-y-antitranspirantes",
                }
            ],
        }
    )
    unrelated_url = _product_search_url(
        {
            "from": 0,
            "to": 19,
            "selectedFacets": [
                {
                    "key": "c",
                    "value": "abarrotes",
                }
            ],
        }
    )

    scraper._consider_product_search_template(
        unrelated_url,
        records_filtered=999,
        product_count=20,
        source="response",
    )
    scraper._consider_product_search_template(
        category_url,
        records_filtered=378,
        product_count=20,
        source="response",
    )

    assert scraper._product_search_template_url == category_url
    assert scraper.run_meta["product_search_template"]["affinity"] >= 10


def test_request_affinity_protects_template_before_response_is_parsed():
    scraper = ChedrauiScraper(require_store_context=False)
    scraper._product_search_category = _category()

    category_url = _product_search_url(
        {
            "from": 0,
            "to": 19,
            "selectedFacets": [
                {
                    "key": "c",
                    "value": "desodorantes-y-antitranspirantes",
                }
            ],
        }
    )
    auxiliary_url = _product_search_url(
        {
            "from": 0,
            "to": 0,
            "fullText": "recomendado",
        }
    )

    scraper._consider_product_search_template(
        category_url,
        source="request",
    )
    scraper._consider_product_search_template(
        auxiliary_url,
        source="request",
    )

    assert scraper._product_search_template_url == category_url
    assert scraper.run_meta["product_search_template"]["span"] == 20


def test_records_filtered_consistency_rejects_catastrophic_mismatch():
    assert ChedrauiScraper._records_filtered_consistent(302, 300)
    assert ChedrauiScraper._records_filtered_consistent(340, 300)
    assert not ChedrauiScraper._records_filtered_consistent(1, 300)
    assert not ChedrauiScraper._records_filtered_consistent(900, 300)


def test_best_template_uses_live_catalogue_reference_when_affinity_is_equal():
    scraper = ChedrauiScraper(require_store_context=False)
    scraper._product_search_category = _category()

    near_url = _product_search_url(
        {
            "from": 0,
            "to": 19,
            "selectedFacets": [{"key": "c", "value": "category-123"}],
        }
    )
    tiny_url = _product_search_url(
        {
            "from": 0,
            "to": 0,
            "selectedFacets": [{"key": "c", "value": "category-456"}],
        }
    )

    scraper._consider_product_search_template(
        near_url,
        records_filtered=365,
        product_count=20,
        source="response",
    )
    scraper._consider_product_search_template(
        tiny_url,
        records_filtered=1,
        product_count=1,
        source="response",
    )

    selected = scraper._select_best_product_search_template(363)
    assert selected == near_url
    assert scraper.run_meta["product_search_template"][
        "records_filtered"
    ] == 365


def test_best_template_prefers_response_over_request_when_live_evidence_exists():
    scraper = ChedrauiScraper(require_store_context=False)
    scraper._product_search_category = _category()

    request_url = _product_search_url(
        {
            "from": 0,
            "to": 19,
            "selectedFacets": [
                {
                    "key": "c",
                    "value": "desodorantes-y-antitranspirantes",
                }
            ],
        }
    )
    response_url = _product_search_url(
        {
            "from": 0,
            "to": 19,
            "selectedFacets": [
                {
                    "key": "c",
                    "value": "desodorantes-y-antitranspirantes",
                }
            ],
            "orderBy": "OrderByTopSaleDESC",
        }
    )

    scraper._consider_product_search_template(
        request_url,
        source="request",
    )
    scraper._consider_product_search_template(
        response_url,
        records_filtered=378,
        product_count=20,
        source="response",
    )

    selected = scraper._select_best_product_search_template(378)
    assert selected == response_url
    assert scraper.run_meta["product_search_template"]["source"] == "response"


def test_dom_floor_can_reject_single_product_template_without_visible_counter():
    scraper = ChedrauiScraper(require_store_context=False)
    assert not scraper._records_filtered_consistent(1, 20)
    assert scraper._records_filtered_consistent(18, 20)


def test_chedraui_recovery_rechecks_store_after_template_reload():
    source = __import__("pathlib").Path(
        "scraper/retailers/chedraui_polanco_api.py"
    ).read_text(encoding="utf-8")
    refresh = source.split(
        "def _refresh_product_search_template", 1
    )[1].split("def _capture_product_search_request", 1)[0]
    assert "_verify_store_context" in refresh
    assert "store_context_lost_after_reload" in refresh


def test_chedraui_load_page_self_heals_template_drift():
    source = __import__("pathlib").Path(
        "scraper/retailers/chedraui_polanco_api.py"
    ).read_text(encoding="utf-8")
    load_page = source.split(
        "def _load_page_rows", 1
    )[1].split("__all__", 1)[0]
    assert "template_records_mismatch:" in source
    assert "_refresh_product_search_template" in load_page
    assert "structured_catalogue_reference_source" in load_page
