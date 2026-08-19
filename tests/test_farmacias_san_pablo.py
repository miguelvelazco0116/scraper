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
    assert FarmaciasSanPabloScraper._page_url(url, 1) == url
    assert FarmaciasSanPabloScraper._page_url(url, 3).endswith("/c/030040003?currentPage=3")
    url_with_query = url + "?foo=bar"
    page2 = FarmaciasSanPabloScraper._page_url(url_with_query, 2)
    assert "foo=bar" in page2
    assert "currentPage=2" in page2


def test_san_pablo_block_detection():
    assert FarmaciasSanPabloScraper._is_blocked(403, "Access Denied", "")
    assert FarmaciasSanPabloScraper._is_blocked(200, "", "You don't have permission to access this server")
    assert not FarmaciasSanPabloScraper._is_blocked(200, "Farmacias San Pablo", "Catálogo de productos")


def test_san_pablo_sku_from_product_url():
    url = "https://www.farmaciasanpablo.com.mx/medicamentos/gripe-y-tos/descongestionantes/sterimar-nasal/p/000000000000700142"
    assert FarmaciasSanPabloScraper._sku_from_url(url) == "700142"


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
