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
