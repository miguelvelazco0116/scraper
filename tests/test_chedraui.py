import base64
import json
from urllib.parse import parse_qs, urlsplit

from scraper.config import Category, Location, load_categories
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

TEPEYAC = Location(
    id="chedraui-tepeyac",
    city="Gustavo A. Madero",
    state="CDMX",
    country="México",
    postal_code="07820",
    store="Chedraui México Tepeyac",
    store_id="173",
)

CANCUN_KABAH = Location(
    id="chedraui-cancun-kabah",
    city="Cancún",
    state="Quintana Roo",
    country="México",
    postal_code="77501",
    store="Chedraui Cancún Kabah",
    store_id="255",
)

PUEBLA_CENTRO_SUR = Location(
    id="chedraui-puebla-centro-sur",
    city="Puebla",
    state="Puebla",
    country="México",
    postal_code="72490",
    store="Chedraui Puebla Centro Sur",
    store_id="76",
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

def test_chedraui_generic_store_contexts():
    cases = [
        (
            TEPEYAC,
            "Recoger en Chedraui México Tepeyac 07820",
            '{"storeName":"Chedraui Mexico Tepeyac","storeId":"173"}',
        ),
        (
            CANCUN_KABAH,
            "Recoger en Chedraui Cancún Kabah 77501",
            '{"storeName":"Selecto Cancun Kabah","storeId":"255"}',
        ),
        (
            PUEBLA_CENTRO_SUR,
            "Recoger en Chedraui Puebla Centro Sur 72490",
            '{"storeName":"Puebla Centro Sur","storeId":"76"}',
        ),
    ]
    for location, text, state_blob in cases:
        assert ChedrauiScraper._store_context_in_text(text, location)
        assert ChedrauiScraper._store_context_in_state_blob(
            state_blob,
            location,
        )


def test_chedraui_generic_pickup_points_match_location():
    cases = [
        (TEPEYAC, "CHEDRAUI MEXICO TEPEYAC", "07820"),
        (CANCUN_KABAH, "CHEDRAUI SELECTO CANCUN KABAH", "77501"),
        (PUEBLA_CENTRO_SUR, "CHEDRAUI PUEBLA CENTRO SUR", "72490"),
    ]
    for location, friendly_name, postal_code in cases:
        item = {
            "pickupPoint": {
                "friendlyName": friendly_name,
                "address": {"postalCode": postal_code},
            }
        }
        assert ChedrauiScraper._pickup_matches_location(item, location)


def test_chedraui_generic_orderform_store_ids():
    for location in (TEPEYAC, CANCUN_KABAH, PUEBLA_CENTRO_SUR):
        assert ChedrauiScraper._orderform_matches_location(
            {"storeId": location.store_id},
            location,
        )

def test_chedraui_store_id_matches_zero_padded_vtex_ids():
    assert ChedrauiScraper._location_store_id_matches_blob(
        "chedrauimx0255",
        CANCUN_KABAH,
    )
    assert ChedrauiScraper._location_store_id_matches_blob(
        "pickup-0173",
        TEPEYAC,
    )
    assert ChedrauiScraper._location_store_id_matches_blob(
        "dock-0076",
        PUEBLA_CENTRO_SUR,
    )


def test_chedraui_pickup_matches_by_zero_padded_store_id():
    item = {
        "pickupPoint": {
            "id": "chedrauimx0255",
            "friendlyName": "Sucursal Kabah",
            "address": {"postalCode": "77501"},
        }
    }
    assert ChedrauiScraper._pickup_matches_location(
        item,
        CANCUN_KABAH,
    )


def test_chedraui_pickup_exact_store_id_overrides_postal_mismatch():
    item = {
        "pickupPoint": {
            "id": "chedrauimx0173_PP0173",
            "friendlyName": "CHEDRAUI TEPEYAC",
            "address": {"postalCode": "07800"},
        }
    }
    assert ChedrauiScraper._pickup_matches_location(
        item,
        TEPEYAC,
    )



def test_chedraui_all_locations_use_same_directory_selector(monkeypatch):
    scraper = ChedrauiPolancoScraper()
    calls = []

    def fake_directory(page, location):
        calls.append(location.id)
        return True, "directory_ui"

    monkeypatch.setattr(
        scraper,
        "_try_select_store_directory_generic",
        fake_directory,
    )

    for location in (
        POLANCO,
        TEPEYAC,
        CANCUN_KABAH,
        PUEBLA_CENTRO_SUR,
    ):
        assert scraper._try_select_store_ui(None, location) == (
            True,
            "directory_ui",
        )

    assert calls == [
        "chedraui-polanco",
        "chedraui-tepeyac",
        "chedraui-cancun-kabah",
        "chedraui-puebla-centro-sur",
    ]


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
    assert row["availability_status"] == "AVAILABLE"
    assert row["is_available"] is True
    assert "AvailableQuantity=10" in row["availability_raw"]
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

    def fake_api(
        self,
        page,
        category,
        location,
        page_number,
        target_rows=None,
    ):
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

    def fake_api(
        self,
        page,
        category,
        location,
        page_number,
        target_rows=None,
    ):
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

    def fake_api(
        self,
        page,
        category,
        location,
        page_number,
        target_rows=None,
    ):
        return [], {
            "mode": "productSearchV3",
            "rows": 0,
            "reason": "template_not_captured",
        }

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



def test_chedraui_pickup_candidate_matches_polanco():
    item = {
        "distance": 1.2,
        "pickupPoint": {
            "id": "pickup-polanco",
            "friendlyName": "Chedraui Selecto México Polanco",
            "address": {
                "postalCode": "11500",
                "neighborhood": "Polanco",
            },
        },
    }
    assert ChedrauiScraper._pickup_matches_location(item, POLANCO)


def test_chedraui_zero_available_quantity_is_unavailable():
    scraper = ChedrauiAPIScraper()
    scraper._active_store_context_method = "persistent_profile"
    payload = {
        "data": {
            "productSearch": {
                "recordsFiltered": 1,
                "products": [
                    {
                        "productId": "999",
                        "productName": "Producto agotado",
                        "brand": "Marca",
                        "link": "/producto-agotado-999/p",
                        "items": [
                            {
                                "sellers": [
                                    {
                                        "sellerDefault": True,
                                        "commertialOffer": {
                                            "Price": 50,
                                            "ListPrice": 50,
                                            "AvailableQuantity": 0,
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
    rows, _ = scraper._rows_from_product_search_payload(payload, LAUNDRY, POLANCO)
    assert len(rows) == 1
    assert rows[0]["availability_status"] == "UNAVAILABLE"
    assert rows[0]["is_available"] is False
    assert rows[0]["pickup_available"] is False



def test_chedraui_orderform_matches_polanco():
    payload = {
        "storeId": "232",
        "checkedInPickupPointId": "pickup-polanco",
        "shippingData": {
            "logisticsInfo": [
                {
                    "selectedDeliveryChannel": "pickup-in-point",
                    "pickupPointId": "pickup-polanco",
                    "pickupStoreInfo": {
                        "friendlyName": "Chedraui Selecto México Polanco",
                        "address": {
                            "postalCode": "11500",
                            "neighborhood": "Polanco",
                            "city": "Miguel Hidalgo",
                        },
                    },
                }
            ]
        },
    }
    assert ChedrauiScraper._orderform_matches_location(payload, POLANCO)


def test_chedraui_orderform_rejects_other_store():
    payload = {
        "storeId": "999",
        "shippingData": {
            "logisticsInfo": [
                {
                    "selectedDeliveryChannel": "pickup-in-point",
                    "pickupStoreInfo": {
                        "friendlyName": "Chedraui Selecto Santa Fe",
                        "address": {
                            "postalCode": "01219",
                            "neighborhood": "Santa Fe",
                        },
                    },
                }
            ]
        },
    }
    assert not ChedrauiScraper._orderform_matches_location(payload, POLANCO)



def test_chedraui_zero_stock_without_price_is_retained():
    scraper = ChedrauiAPIScraper()
    scraper._active_store_context_method = "orderform"
    payload = {
        "data": {
            "productSearch": {
                "recordsFiltered": 1,
                "products": [
                    {
                        "productId": "1000",
                        "productName": "Producto sin precio por agotado",
                        "brand": "Marca",
                        "link": "/producto-agotado-1000/p",
                        "items": [
                            {
                                "sellers": [
                                    {
                                        "sellerDefault": True,
                                        "commertialOffer": {
                                            "Price": None,
                                            "ListPrice": None,
                                            "AvailableQuantity": 0,
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

    rows, _ = scraper._rows_from_product_search_payload(
        payload,
        LAUNDRY,
        POLANCO,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["price_current"] is None
    assert row["availability_status"] == "UNAVAILABLE"
    assert row["is_available"] is False
    assert row["pickup_available"] is False


def test_chedraui_retries_structured_page_until_target_is_complete():
    scraper = ChedrauiAPIScraper()
    scraper._product_search_template_url = "https://example.test/?extensions=eyJ2YXJpYWJsZXMiOiAiZTMwPSJ9"

    class FakeResponse:
        def __init__(self, status, payload):
            self.status = status
            self._payload = payload

        def json(self):
            return self._payload

    class FakeRequest:
        def __init__(self):
            self.calls = 0

        def get(self, url, timeout, headers):
            self.calls += 1
            if self.calls < 3:
                products = [{"productId": str(i)} for i in range(10)]
            else:
                products = [{"productId": str(i)} for i in range(20)]
            return FakeResponse(
                200,
                {"data": {"productSearch": {"products": products, "recordsFiltered": 303}}},
            )

    class FakeContext:
        def __init__(self):
            self.request = FakeRequest()

    class FakePage:
        def __init__(self):
            self.context = FakeContext()

        def wait_for_timeout(self, _ms):
            return None

    rows10 = [{"sku": str(i)} for i in range(10)]
    rows20 = [{"sku": str(i)} for i in range(20)]
    calls = {"n": 0}

    def fake_rows(payload, category, location):
        calls["n"] += 1
        return (rows10, 303) if calls["n"] < 3 else (rows20, 303)

    scraper._rewrite_product_search_range = lambda url, start, end: url
    scraper._rows_from_product_search_payload = fake_rows

    rows, info = scraper._recover_page_from_product_search(
        FakePage(),
        LAUNDRY,
        POLANCO,
        page_number=1,
        target_rows=20,
    )

    assert len(rows) == 20
    assert info["rows"] == 20
    assert len(info["attempts"]) == 3
    assert info["attempts"][-1]["accepted"] is True


def test_chedraui_candidate_categories_are_separate_from_production():
    active = {
        item.id
        for item in load_categories("config/chedraui/categories.yaml")
    }
    candidates = {
        item.id
        for item in load_categories(
            "config/chedraui/candidate_categories.yaml"
        )
    }

    assert active == {"higiene-bucal", "lavanderia"}
    assert candidates == {
        "desodorantes-y-antitranspirantes",
        "depilacion",
        "respiratorios",
    }
    assert active.isdisjoint(candidates)


def test_chedraui_candidate_category_hierarchy():
    candidates = {
        item.id: item
        for item in load_categories(
            "config/chedraui/candidate_categories.yaml"
        )
    }

    deodorants = candidates["desodorantes-y-antitranspirantes"]
    assert deodorants.department == "Cuidado e higiene personal"
    assert deodorants.name == "Cuidado corporal"
    assert deodorants.subcategory == "Desodorantes y antitranspirantes"

    depilation = candidates["depilacion"]
    assert depilation.department == "Belleza"
    assert depilation.name == "Afeitado y depilación"
    assert depilation.subcategory == "Depilación"

    respiratory = candidates["respiratorios"]
    assert respiratory.department == "Farmacia"
    assert respiratory.name == "Medicamentos"
    assert respiratory.subcategory == "Respiratorios"
