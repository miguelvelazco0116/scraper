from scraper.retailers.chedraui import ChedrauiScraper


def test_chedraui_generic_no_disponible_is_unknown():
    result = ChedrauiScraper._availability_from_card_text(
        "Entrega a domicilio no disponible"
    )
    assert result["availability_status"] == "UNKNOWN"


def test_chedraui_explicit_agotado_is_unavailable():
    result = ChedrauiScraper._availability_from_card_text(
        "Producto agotado"
    )
    assert result["availability_status"] == "UNAVAILABLE"
    assert result["is_available"] is False


def test_chedraui_add_action_is_available():
    result = ChedrauiScraper._availability_from_card_text(
        "Enjuague bucal $89 Agregar al carrito"
    )
    assert result["availability_status"] == "AVAILABLE"
    assert result["is_available"] is True
