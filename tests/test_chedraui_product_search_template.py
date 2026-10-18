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
