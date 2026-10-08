from scraper.retailers.soriana import SorianaScraper


def test_soriana_generic_no_disponible_does_not_mean_out_of_stock():
    result = SorianaScraper._availability_from_card_text(
        "Entrega a domicilio No disponible | $99.00"
    )
    assert result["availability_status"] == "UNKNOWN"
    assert result["is_available"] is None


def test_soriana_explicit_out_of_stock():
    result = SorianaScraper._availability_from_card_text(
        "Producto agotado temporalmente"
    )
    assert result["availability_status"] == "UNAVAILABLE"
    assert result["is_available"] is False


def test_soriana_add_action_means_available():
    result = SorianaScraper._availability_from_card_text(
        "Pasta dental $45 Agregar"
    )
    assert result["availability_status"] == "AVAILABLE"
    assert result["is_available"] is True
