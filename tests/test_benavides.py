from scraper.config import load_categories, load_locations
from scraper.retailers.benavides import BenavidesScraper


def test_benavides_categories():
    categories = {
        x.id: x
        for x in load_categories("config/benavides/categories.yaml")
    }
    assert set(categories) == {
        "medicamentos-respiratorios",
        "bienestar-sexual",
        "higiene-bucal",
    }
    assert categories["medicamentos-respiratorios"].url.endswith(
        "/medicamentos-respiratorios"
    )
    assert categories["bienestar-sexual"].url.endswith(
        "/bienestar-sexual"
    )
    assert categories["higiene-bucal"].url.endswith(
        "/cuidado-personal-higiene-bucal"
    )


def test_benavides_online_context():
    locations = {x.id: x for x in load_locations()}
    location = locations["benavides-online"]
    assert location.city == "Catálogo online"
    assert location.state == "Nacional"
    assert location.store == "Farmacias Benavides online"


def test_benavides_page_url():
    url = "https://www.benavides.com.mx/medicamentos-respiratorios"
    assert BenavidesScraper._page_url(url, 1) == url
    assert BenavidesScraper._page_url(url, 2).endswith(
        "/medicamentos-respiratorios?p=2"
    )


def test_benavides_target_count():
    assert BenavidesScraper._target_count(
        "Respiratorios\nArtículos 1-12 de 456\nOrdenar por"
    ) == 456
    assert BenavidesScraper._target_count(
        "Higiene bucal\nArtículos 175"
    ) is None


def test_benavides_detail_parser_promotional_unavailable():
    text = """
Farmacias Benavides 100 mcg Salbutamol Aerosol 200 Dosis
SKU
1042146
Promociones con tu tarjeta
ACUMULA 3 pzas y recibe 1+ Gratis
Precios y disponibilidad válidos en tienda en línea, sujetos a cambio sin previo aviso.
$69.00 Precio Regular $92.00
*Descuento aplicado
Pieza disponible
El producto no está disponible en la región seleccionada.
Agotado
Información del producto
Marca
Farmacias Benavides
Marca Propia
Sí
Promociones | ACUMULA 3 pzas y recibe 1+ Gratis A SOLO $69
Tipo de entrega | Retiro en Farmacia y Servicio a Domicilio
"""
    result = BenavidesScraper._parse_detail(
        text,
        "100 mcg Salbutamol Aerosol",
        None,
    )
    assert result["sku"] == "1042146"
    assert result["price_current"] == 69.0
    assert result["price_regular"] == 92.0
    assert result["brand"] == "Farmacias Benavides"
    assert result["is_available"] == 0
    assert result["availability_status"] == "UNAVAILABLE"


def test_benavides_detail_parser_available():
    text = """
Producto Dental
SKU
1234567
Precios y disponibilidad válidos en tienda en línea, sujetos a cambio sin previo aviso.
$125.00
Pieza disponible
Información del producto
Marca
Colgate
Marca Propia
No
"""
    result = BenavidesScraper._parse_detail(
        text,
        "Producto Dental",
        None,
    )
    assert result["sku"] == "1234567"
    assert result["price_current"] == 125.0
    assert result["price_regular"] == 125.0
    assert result["brand"] == "Colgate"
    assert result["is_available"] == 1
    assert result["availability_status"] == "AVAILABLE"
