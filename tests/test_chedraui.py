import base64
import json
from urllib.parse import parse_qs, urlsplit

from scraper.config import Category, Location
from scraper.retailers.chedraui import ChedrauiScraper
from scraper.retailers.chedraui_polanco import ChedrauiScraper as ChedrauiPolancoScraper
from scraper.retailers.chedraui_polanco_api import ChedrauiScraper as ChedrauiAPIScraper


POLANCO = Location(
    id="chedraui-polanco",
    city="Miguel Hidalgo",
    state="CDMX",
    country="México",
    postal_code="11500",
    store="Chedraui Selecto México Polanco",
    store_id="232",
)

LAUNDRY = Category(
    id="lavanderia",
    department="Supermercado",
    name="Limpieza del hogar",
    subcategory="Lavandería",
    sub_subcategory=None,
    url="https://www.chedraui.com.mx/supermercado/limpieza-del-hogar/lavanderia",
)


def test_chedraui_paged_url():
    base = "https://www.chedraui.com.mx/cuidado-e-higiene-personal/higiene-bucal"
    assert ChedrauiScraper._paged_url(base, 1) == base
    assert ChedrauiScraper._paged_url(base, 2).endswith("?page=2")


def test_chedraui_store_context_from_page_text():
    text = "Recoger en Chedraui Selecto México Polanco 11500"
    assert ChedrauiScraper._store_context_in_text(text, POLANCO)


def test_chedraui_store_context_from_browser_state():
    blob = '{"storeName":"Selecto Polanco","storeId":"232"}'
    assert ChedrauiScraper._store_context_in_state_blob(blob, POLANCO)


def test_chedraui_rejects_unrelated_store():
    text = "Recoger en Chedraui Selecto México Santa Fe 01219"
    assert not ChedrauiScraper._store_context_in_text(text, POLANCO)


def test_chedraui_brand_inference_uses_word_boundaries():
    assert ChedrauiPolancoScraper._infer_brand("Detergente Ace Líquido 2 L") == "Ace"
    assert ChedrauiPolancoScraper._infer_brand("Portacepillo Dental CoolxShock") is None
    assert ChedrauiPolancoScraper._infer_brand("Gel Dental Fluoxytil Lacer Fresa 75ml") is None


def test_chedraui_product_search_range_rewrite():
    variables = {
        "query": "supermercado/limpieza-del-hogar/lavanderia",
        "from": 320,
        "to": 339,
    }
    extensions = {
        "persistedQuery": {"version": 1, "sha256Hash": "abc"},
        "variables": base64.b64encode(json.dumps(variables).encode()).decode(),
    }
    url = (
        "https://www.chedraui.com.mx/_v/segment/graphql/v1?"
        "operationName=productSearchV3&variables=%7B%7D&extensions="
        + __import__("urllib.parse").parse.quote(json.dumps(extensions))
    )
    rewritten = ChedrauiAPIScraper._rewrite_product_search_range(url, 340, 359)
    qs = parse_qs(urlsplit(rewritten).query)
    decoded_extensions = json.loads(qs["extensions"][0])
    decoded_variables = json.loads(base64.b64decode(decoded_extensions["variables"]).decode())
    assert decoded_variables["from"] == 340
    assert decoded_variables["to"] == 359


def test_chedraui_product_search_payload_maps_to_output():
    scraper = ChedrauiAPIScraper()
    scraper._active_store_context_method = "browser_state_after_directory_ui"
    payload = {
        "data": {
            "productSearch": {
                "recordsFiltered": 382,
                "products": [
                    {
                        "productId": "3908355",
                        "productName": "Suavizante Suavitel Primavera 500ml",
                        "brand": "Suavitel",
                        "link": "/suavizante-suavitel-primavera-500ml-3908355/p",
                        "items": [
                            {
                                "sellers": [
                                    {
                                        "sellerDefault": True,
                                        "commertialOffer": {
                                            "Price": 50,
                                            "ListPrice": 74,
                                            "AvailableQuantity": 10,
                                            "discountHighlights": [],
                                            "teasers": [],
                                        },
                                    }
                                ]
                            }
                        ],
                    }
                ],
            }
        }
    }
    rows, total = scraper._rows_from_product_search_payload(payload, LAUNDRY, POLANCO)
    assert total == 382
    assert len(rows) == 1
    row = rows[0]
    assert row["sku"] == "3908355"
    assert row["brand"] == "Suavitel"
    assert row["price_current"] == 50
    assert row["price_regular"] == 74
    assert row["store_id"] == "232"
    assert row["store_context_verified"] is True
    assert "productSearchV3" in row["store_context_method"]


def test_chedraui_product_search_is_authoritative_for_prices(monkeypatch):
    scraper = ChedrauiAPIScraper()
    html_rows = [
        {
            "sku": "3021491",
            "product": "Pasta Dental Arm & Hammer",
            "price_current": 17.0,
            "price_regular": 1299.0,
            "promotion": "PromociónSí (111)",
        }
    ]
    api_rows = [
        {
            "sku": "3021491",
            "product": "Pasta Dental Arm & Hammer",
            "price_current": 89.0,
            "price_regular": 99.0,
            "promotion": "Precio promocional",
        }
    ]

    def fake_html(self, page, category, location, page_number, target_rows):
        return category.url, html_rows, [{"mode": "html", "rows": 1}]

    def fake_api(self, page, category, location, page_number):
        return api_rows, {"mode": "productSearchV3", "rows": 1}

    monkeypatch.setattr(ChedrauiPolancoScraper, "_load_page_rows", fake_html)
    monkeypatch.setattr(
        ChedrauiAPIScraper,
        "_recover_page_from_product_search",
        fake_api,
    )

    _, rows, attempts = scraper._load_page_rows(
        None, LAUNDRY, POLANCO, page_number=1, target_rows=None
    )

    assert rows == api_rows
    assert rows[0]["price_current"] == 89.0
    assert rows[0]["price_regular"] == 99.0
    assert rows[0]["promotion"] != "PromociónSí (111)"
    assert any(x.get("mode") == "productSearchV3" for x in attempts)
    assert scraper.run_meta["api_price_pages"] == [1]


def test_chedraui_rejects_html_prices_when_structured_request_exists(monkeypatch):
    scraper = ChedrauiAPIScraper()
    scraper._product_search_template_url = "https://example.test/?operationName=productSearchV3"

    html_rows = [
        {
            "sku": "3021491",
            "product": "Pasta Dental Arm & Hammer",
            "price_current": 17.0,
            "price_regular": 1299.0,
            "promotion": "PromociónSí (111)",
        }
    ]

    def fake_html(self, page, category, location, page_number, target_rows):
        return category.url, html_rows, [{"mode": "html", "rows": 1}]

    def fake_api(self, page, category, location, page_number):
        return [], {
            "mode": "productSearchV3",
            "rows": 0,
            "status": 200,
            "reason": "no_structured_price_rows",
        }

    monkeypatch.setattr(ChedrauiPolancoScraper, "_load_page_rows", fake_html)
    monkeypatch.setattr(
        ChedrauiAPIScraper,
        "_recover_page_from_product_search",
        fake_api,
    )

    _, rows, attempts = scraper._load_page_rows(
        None, LAUNDRY, POLANCO, page_number=16, target_rows=None
    )

    assert rows == []
    assert any(x.get("mode") == "productSearchV3" for x in attempts)
    assert scraper.run_meta["structured_price_missing_pages"][0]["page"] == 16
    assert scraper.run_meta["structured_price_missing_pages"][0]["html_rows_rejected"] == 1


def test_chedraui_filters_category_facet_from_legacy_html_fallback(monkeypatch):
    scraper = ChedrauiAPIScraper()

    html_rows = [
        {
            "sku": "bad",
            "product": "Producto contaminado",
            "price_current": 17.0,
            "price_regular": 1299.0,
            "promotion": "PromociónSí (111)",
        },
        {
            "sku": "ok",
            "product": "Producto válido",
            "price_current": 100.0,
            "price_regular": 125.0,
            "promotion": "2x$125",
        },
    ]

    def fake_html(self, page, category, location, page_number, target_rows):
        return category.url, html_rows, [{"mode": "html", "rows": 2}]

    def fake_api(self, page, category, location, page_number):
        return [], {"mode": "productSearchV3", "rows": 0, "reason": "template_not_captured"}

    monkeypatch.setattr(ChedrauiPolancoScraper, "_load_page_rows", fake_html)
    monkeypatch.setattr(
        ChedrauiAPIScraper,
        "_recover_page_from_product_search",
        fake_api,
    )

    _, rows, _ = scraper._load_page_rows(
        None, LAUNDRY, POLANCO, page_number=1, target_rows=None
    )

    assert [row["sku"] for row in rows] == ["ok"]
    assert scraper.run_meta["html_facet_rows_rejected"] == [{"page": 1, "rows": 1}]


def test_chedraui_click_text_skips_hidden_duplicate():
    class FakeNode:
        def __init__(self, visible, click_ok=True):
            self.visible = visible
            self.click_ok = click_ok
            self.clicked = False

        def is_visible(self):
            return self.visible

        def click(self, timeout=None):
            if not self.click_ok:
                raise RuntimeError("not clickable")
            self.clicked = True

    class FakeNodes:
        def __init__(self, nodes):
            self.nodes = nodes

        def count(self):
            return len(self.nodes)

        def nth(self, i):
            return self.nodes[i]

    class FakePage:
        def __init__(self):
            self.hidden = FakeNode(False)
            self.visible = FakeNode(True)

        def get_by_text(self, label, exact=False):
            assert label == "Recoger en"
            return FakeNodes([self.hidden, self.visible])

    page = FakePage()

    assert ChedrauiScraper._click_text(page, ("Recoger en",))
    assert page.hidden.clicked is False
    assert page.visible.clicked is True
