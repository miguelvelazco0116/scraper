from scraper.config import load_categories, load_locations
from scraper.retailers.farmacias_guadalajara import FarmaciasGuadalajaraScraper


def test_farmacias_guadalajara_categories():
    categories = {x.id: x for x in load_categories("config/farmacias-guadalajara/categories.yaml")}

    assert set(categories) == {"vias-respiratorias", "lavanderia", "cuidado-bucal", "preservativos"}

    vias = categories["vias-respiratorias"]
    assert vias.department == "Medicina"
    assert vias.name == "Respiratorio"
    assert vias.subcategory == "Vías respiratorias"
    assert vias.url.endswith("/farmacia/medicina/respiratorio/vias-respiratorias")

    lavanderia = categories["lavanderia"]
    assert lavanderia.department == "Super"
    assert lavanderia.name == "Hogar"
    assert lavanderia.subcategory == "Lavandería"
    assert lavanderia.url.endswith("/super/hogar/lavanderia-")

    bucal = categories["cuidado-bucal"]
    assert bucal.department == "Super"
    assert bucal.name == "Higiene y belleza"
    assert bucal.subcategory == "Cuidado bucal"

    preservativos = categories["preservativos"]
    assert preservativos.department == "Farmacia"
    assert preservativos.name == "Salud sexual"
    assert preservativos.subcategory == "Preservativos"


def test_farmacias_guadalajara_online_context():
    locations = {x.id: x for x in load_locations()}
    location = locations["fg-online"]
    assert location.city == "Catálogo online"
    assert location.state == "Nacional"
    assert location.postal_code is None
    assert location.store is None
    assert location.store_id is None


def test_farmacias_guadalajara_extract_sku():
    url = "https://www.farmaciasguadalajara.com/salud-sexual/preservativos/preservativos-lubricados-prudence-zero--10-pzas.-1400185.html"
    assert FarmaciasGuadalajaraScraper.extract_sku(url) == "1400185"


def test_farmacias_guadalajara_prices():
    current, regular, promo = FarmaciasGuadalajaraScraper._prices_from_text(
        "PRUDENCE\nPreservativos Zero\n$179.00\n$134.25\nAgregar"
    )
    assert current == 134.25
    assert regular == 179.0
    assert promo == "Precio promocional"

    current, regular, promo = FarmaciasGuadalajaraScraper._prices_from_text(
        "COLGATE\nPasta dental\n$65.00\nAgregar"
    )
    assert current == 65.0
    assert regular == 65.0
    assert promo is None


def test_farmacias_guadalajara_brand_boundaries():
    assert FarmaciasGuadalajaraScraper._infer_brand("Detergente Ace Líquido", "Detergente Ace Líquido") == "Ace"
    assert FarmaciasGuadalajaraScraper._infer_brand("Portacepillo Dental", "Portacepillo Dental") is None
