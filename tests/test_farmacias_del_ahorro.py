from scraper.config import load_categories, load_locations
from scraper.retailers.farmacias_del_ahorro import FarmaciasDelAhorroScraper


def test_farmacias_del_ahorro_categories():
    categories = {x.id: x for x in load_categories("config/farmacias-del-ahorro/categories.yaml")}
    assert set(categories) == {"congestion-nasal", "preservativos", "enjuagues-bucales", "cremas-dentales"}

    congestion = categories["congestion-nasal"]
    assert congestion.department == "Farmacia"
    assert congestion.name == "Gripa y tos"
    assert congestion.subcategory == "Congestión nasal"
    assert congestion.url.endswith("/farmacia/gripa-y-tos/congestion-nasal.html")

    preservativos = categories["preservativos"]
    assert preservativos.department == "Bienestar sexual"
    assert preservativos.name == "Preservativos"
    assert preservativos.subcategory is None
    assert preservativos.url.endswith("/bienestar-sexual/preservativos.html")

    enjuagues = categories["enjuagues-bucales"]
    assert enjuagues.department == "Cuidado personal"
    assert enjuagues.name == "Higiene bucal"
    assert enjuagues.subcategory == "Enjuagues bucales"

    cremas = categories["cremas-dentales"]
    assert cremas.department == "Cuidado personal"
    assert cremas.name == "Higiene bucal"
    assert cremas.subcategory == "Cremas dentales"


def test_farmacias_del_ahorro_online_context():
    locations = {x.id: x for x in load_locations()}
    location = locations["fahorro-online"]
    assert location.city == "Catálogo online"
    assert location.state == "Nacional"
    assert location.postal_code is None
    assert location.store is None
    assert location.store_id is None


def test_farmacias_del_ahorro_prices():
    current, regular, promo = FarmaciasDelAhorroScraper._prices_from_card(
        "MXN $67.50", "MXN $90.00", "25% de descuento Precio habitual MXN $90.00 Precio especial MXN $67.50"
    )
    assert current == 67.5
    assert regular == 90.0
    assert promo == "25% de descuento | Precio promocional"

    current, regular, promo = FarmaciasDelAhorroScraper._prices_from_card(
        "MXN $68.00", None, "Precio habitual MXN $68.00 MXN $68.00"
    )
    assert current == 68.0
    assert regular == 68.0
    assert promo is None


def test_farmacias_del_ahorro_brand_boundaries():
    assert FarmaciasDelAhorroScraper._infer_brand("Crema Dental Colgate Total", "Crema Dental Colgate Total") == "Colgate"
    assert FarmaciasDelAhorroScraper._infer_brand("Portacolgate Dental", "Portacolgate Dental") is None


def test_farmacias_del_ahorro_page_url():
    url = "https://www.fahorro.com/cuidado-personal/higiene-bucal/cremas-dentales.html"
    assert FarmaciasDelAhorroScraper._page_url(url, 1) == url
    assert FarmaciasDelAhorroScraper._page_url(url, 2).endswith("cremas-dentales.html?p=2")


def test_farmacias_del_ahorro_sku_from_html():
    html = '<div class="product attribute sku"><div class="value" itemprop="sku">7509546000343</div></div>'
    assert FarmaciasDelAhorroScraper._sku_from_html(html) == "7509546000343"


def test_farmacias_del_ahorro_extracts_empathy_category_id():
    html = '''
    <script type="text/x-magento-init">
    {"component":"Infinite_EmpathySearch/js/view/search-list-component",
     "searchResultsEndpointUrl":"https://api.empathy.co/search/v1/query/fda/browse",
     "categoryId":"8196"}
    </script>
    '''
    assert FarmaciasDelAhorroScraper._extract_category_id(html) == "8196"


def test_farmacias_del_ahorro_does_not_false_positive_magento_captcha_module():
    normal_html = '<script src="Magento_Captcha/js/captcha.js"></script><h1>Cremas Dentales</h1>'
    blocked_html = '<html><body>Verify you are human. Press and hold.</body></html>'
    assert FarmaciasDelAhorroScraper._looks_blocked(normal_html) is False
    assert FarmaciasDelAhorroScraper._looks_blocked(blocked_html) is True


def test_farmacias_del_ahorro_maps_empathy_item():
    categories = {x.id: x for x in load_categories("config/farmacias-del-ahorro/categories.yaml")}
    locations = {x.id: x for x in load_locations()}
    item = {
        "sku": "7896009419324",
        "ecommTitle": "Crema Dental Sensodyne Original 90 g",
        "ecommBrand": "SENSODYNE",
        "ecommUrlKey": "crema-dental-sensodyne-original-90-g",
        "currentPrice": 79.0,
        "previousPrice": 90.0,
    }
    row = FarmaciasDelAhorroScraper._row_from_item(
        item, categories["cremas-dentales"], locations["fahorro-online"], "2026-08-19T15:00:00-06:00"
    )
    assert row is not None
    assert row["sku"] == "7896009419324"
    assert row["price_current"] == 79.0
    assert row["price_regular"] == 90.0
    assert row["promotion"] == "12% de descuento | Precio promocional"
    assert row["url"].endswith("/crema-dental-sensodyne-original-90-g.html")
    assert row["store_context_method"] == "online_catalog_empathy_nacional"


def test_farmacias_del_ahorro_missing_previous_price_uses_current():
    categories = {x.id: x for x in load_categories("config/farmacias-del-ahorro/categories.yaml")}
    locations = {x.id: x for x in load_locations()}
    item = {
        "sku": "123",
        "ecommTitle": "Producto",
        "ecommBrand": "Marca",
        "ecommUrlKey": "producto",
        "currentPrice": 100,
        "previousPrice": None,
    }
    row = FarmaciasDelAhorroScraper._row_from_item(
        item, categories["preservativos"], locations["fahorro-online"], "2026-08-19T15:00:00-06:00"
    )
    assert row is not None
    assert row["price_regular"] == 100.0
    assert row["promotion"] is None
