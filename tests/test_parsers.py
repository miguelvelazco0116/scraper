from scraper.parsers import absolute_url, extract_sku, parse_money


def test_parse_money():
    assert parse_money("$1,234.50") == 1234.50
    assert parse_money("$24.50") == 24.50
    assert parse_money(None) is None


def test_extract_sku_from_url():
    assert extract_sku("https://www.soriana.com/algo/371875.html") == "371875"


def test_extract_chedraui_sku_from_url():
    url = "https://www.chedraui.com.mx/Enjuague-Bucal-Colgate-Total-12-Anti-Sarro-500ml-3646798/p"
    assert extract_sku(url) == "3646798"


def test_extract_sku_prefers_data_pid():
    assert extract_sku("https://www.soriana.com/algo/371875.html", "ABC123") == "ABC123"


def test_absolute_url():
    assert absolute_url("/producto/123.html") == "https://www.soriana.com/producto/123.html"
