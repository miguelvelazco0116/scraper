from scraper.availability import (
    AVAILABLE,
    UNAVAILABLE,
    UNKNOWN,
    availability_fields,
    availability_from_mapping,
    availability_from_text,
)


def test_availability_negative_text_has_priority():
    status, available, raw = availability_from_text("Producto no disponible")
    assert status == UNAVAILABLE
    assert available is False
    assert raw == "Producto no disponible"


def test_availability_positive_text():
    status, available, _ = availability_from_text("Agregar al carrito")
    assert status == AVAILABLE
    assert available is True


def test_availability_unknown_text():
    status, available, _ = availability_from_text("Enjuague bucal 500 ml")
    assert status == UNKNOWN
    assert available is None


def test_availability_from_occ_stock_status():
    status, available, raw = availability_from_mapping(
        {"stockLevelStatus": "outOfStock", "stockLevel": 0}
    )
    assert status == UNAVAILABLE
    assert available is False
    assert raw is not None


def test_availability_from_numeric_stock():
    fields = availability_fields(payload={"stock": {"stockLevel": 12}})
    assert fields["availability_status"] == AVAILABLE
    assert fields["is_available"] is True
