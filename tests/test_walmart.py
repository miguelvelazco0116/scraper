from scraper.config import load_categories, load_locations
from scraper.parsers import extract_sku
from scraper.retailers.walmart import WalmartScraper


def test_walmart_categories_hierarchy():
    categories = {x.id: x for x in load_categories("config/walmart/categories.yaml")}

    oral = categories["cuidado-bucal"]
    assert oral.department == "Belleza y cuidado personal"
    assert oral.name == "Higiene y cuidado personal"
    assert oral.subcategory == "Cuidado bucal"
    assert oral.url.endswith("/browse/cuidado-personal/cuidado-bucal/264479_950014")

    laundry = categories["cuidado-de-la-ropa"]
    assert laundry.department == "Limpieza del hogar y cuidado personal"
    assert laundry.name == "Cuidado de la ropa"
    assert laundry.url.endswith("/browse/cuidado-de-la-ropa/3680083")


def test_walmart_sc_toreo_config():
    locations = {x.id: x for x in load_locations()}
    store = locations["sc-toreo"]
    assert store.store == "SC Toreo"
    assert store.store_id == "2344"
    assert store.postal_code == "11220"


def test_walmart_sku_parser():
    url = "https://www.walmart.com.mx/ip/pasta-dental-colgate/00750954607184"
    assert extract_sku(url) == "00750954607184"


def test_walmart_pagination_url():
    url = "https://www.walmart.com.mx/browse/cuidado-de-la-ropa/3680083?facet=x"
    assert WalmartScraper._paged_url(url, 1).endswith("?facet=x")
    assert "page=2" in WalmartScraper._paged_url(url, 2)
