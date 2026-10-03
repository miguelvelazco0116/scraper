from scraper.config import load_categories, load_locations
from scraper.retailers.farmacias_san_pablo import FarmaciasSanPabloScraper


def test_san_pablo_categories():
    categories = {x.id: x for x in load_categories("config/farmacias-san-pablo/categories.yaml")}
    assert set(categories) == {"descongestionantes", "preservativos", "enjuagues-bucales", "pastas-dentales"}

    descong = categories["descongestionantes"]
    assert descong.department == "Medicamentos"
    assert descong.name == "Gripe y tos"
    assert descong.subcategory == "Descongestionantes"
    assert descong.url.endswith("/c/060070004")

    preserv = categories["preservativos"]
    assert preserv.department == "Salud sexual"
    assert preserv.name == "Bienestar sexual"
    assert preserv.subcategory == "Preservativos"

    enjuagues = categories["enjuagues-bucales"]
    assert enjuagues.department == "Cuidado personal y belleza"
    assert enjuagues.name == "Cuidado bucal"
    assert enjuagues.subcategory == "Enjuagues bucales"
    assert enjuagues.url.endswith("/c/030040003")

    pastas = categories["pastas-dentales"]
    assert pastas.department == "Cuidado personal y belleza"
    assert pastas.name == "Cuidado bucal"
    assert pastas.subcategory == "Pastas dentales"
    assert pastas.url.endswith("/c/030040007")


def test_san_pablo_online_context():
    locations = {x.id: x for x in load_locations()}
    location = locations["san-pablo-online"]
    assert location.city == "Catálogo online"
    assert location.state == "Nacional"
    assert location.postal_code is None
    assert location.store is None
    assert location.store_id is None


def test_san_pablo_page_url():
    url = "https://www.farmaciasanpablo.com.mx/cuidado-personal-y-belleza/cuidado-bucal/enjuagues-bucales/c/030040003"
    assert FarmaciasSanPabloScraper._page_url(url, 0) == url
    assert FarmaciasSanPabloScraper._page_url(url, 1).endswith("/c/030040003?currentPage=1")
    assert FarmaciasSanPabloScraper._page_url(url, 3).endswith("/c/030040003?currentPage=3")
    url_with_query = url + "?foo=bar"
    page2 = FarmaciasSanPabloScraper._page_url(url_with_query, 2)
    assert "foo=bar" in page2
    assert "currentPage=2" in page2


def test_san_pablo_block_detection():
    assert FarmaciasSanPabloScraper._is_blocked("Access Denied", "")
    assert FarmaciasSanPabloScraper._is_blocked(
        "", "You don't have permission to access this server"
    )
    assert not FarmaciasSanPabloScraper._is_blocked(
        "Farmacias San Pablo", "Catálogo de productos"
    )


def test_san_pablo_sku_from_product_url():
    url = "https://www.farmaciasanpablo.com.mx/medicamentos/gripe-y-tos/descongestionantes/sterimar-nasal/p/000000000000700142"
    assert FarmaciasSanPabloScraper._sku_from_url(url) == "700142"
    assert FarmaciasSanPabloScraper._code_from_card({"href": url}) == "700142"


def test_san_pablo_price_parser():
    current, regular, promotion = FarmaciasSanPabloScraper._prices_from_text(
        "$319.00 $223.00 30% de descuento"
    )
    assert current == 223.0
    assert regular == 319.0
    assert promotion == "30% de descuento | Precio promocional"

    current, regular, promotion = FarmaciasSanPabloScraper._prices_from_text("$248.00")
    assert current == 248.0
    assert regular == 248.0
    assert promotion is None


def test_san_pablo_brand_boundaries():
    assert FarmaciasSanPabloScraper._infer_brand("Sterimar Nasal 100 ml") == "Stérimar"
    assert FarmaciasSanPabloScraper._infer_brand("Pasta Dental Colgate Total") == "Colgate"
    assert FarmaciasSanPabloScraper._infer_brand("Portacolgate dental") is None


def test_san_pablo_occ_page_url():
    url = (
        "https://api.farmaciasanpablo.com.mx/rest/v2/fsp/products/search-sponsored"
        "?pageSize=48&query=%3Arelevance%3AallCategories%3A030040003"
    )
    page2 = FarmaciasSanPabloScraper._occ_page_url(url, 2)
    assert "pageSize=48" in page2
    assert "currentPage=2" in page2


def test_san_pablo_occ_row_parser():
    categories = {x.id: x for x in load_categories("config/farmacias-san-pablo/categories.yaml")}
    locations = {x.id: x for x in load_locations()}
    category = categories["enjuagues-bucales"]
    location = locations["san-pablo-online"]

    product = {
        "code": "000000000000700142",
        "name": "Sterimar Nasal 100 ml",
        "url": "/medicamentos/gripe-y-tos/descongestionantes/sterimar-nasal/p/000000000000700142",
        "price": {"value": 223.0, "formattedValue": "$223.00 MXN"},
        "basePrice": {"value": 319.0, "formattedValue": "$319.00 MXN"},
        "potentialPromotions": [{"description": "30% de descuento"}],
        "gtmProperties": {"brand": "Stérimar"},
        "stock": {"stockLevelStatus": "inStock", "stockLevel": 7},
    }

    row = FarmaciasSanPabloScraper._row_from_occ_product(
        product,
        category,
        location,
        "2026-10-02T12:00:00-06:00",
    )

    assert row is not None
    assert row["sku"] == "700142"
    assert row["product"] == "Sterimar Nasal 100 ml"
    assert row["brand"] == "Stérimar"
    assert row["price_current"] == 223.0
    assert row["price_regular"] == 319.0
    assert row["url"].endswith("/p/000000000000700142")
    assert "30% de descuento" in row["promotion"]
    assert row["availability_status"] == "AVAILABLE"
    assert row["is_available"] is True
    assert row["store_context_method"] == "san_pablo_occ_search_sponsored"



def test_san_pablo_occ_unavailable_without_price_is_retained():
    categories = {x.id: x for x in load_categories("config/farmacias-san-pablo/categories.yaml")}
    locations = {x.id: x for x in load_locations()}
    row = FarmaciasSanPabloScraper._row_from_occ_product(
        {
            "code": "000000000000999999",
            "name": "Producto agotado",
            "url": "/producto-agotado/p/000000000000999999",
            "price": None,
            "basePrice": None,
            "stock": {"stockLevelStatus": "outOfStock", "stockLevel": 0},
        },
        categories["enjuagues-bucales"],
        locations["san-pablo-online"],
        "2026-10-02T12:00:00-06:00",
    )
    assert row is not None
    assert row["price_current"] is None
    assert row["availability_status"] == "UNAVAILABLE"
    assert row["is_available"] is False
