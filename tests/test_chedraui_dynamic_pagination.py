from scraper.retailers.chedraui_polanco import ChedrauiScraper


class _FakePage:
    pass


def _snapshot(text: str, *, next_available: bool = True):
    scraper = ChedrauiScraper(require_store_context=False)
    scraper._body_text = lambda _page: text
    scraper._paginator_next_available = (
        lambda _page: next_available
    )
    return scraper, scraper._pagination_snapshot(_FakePage())


def test_dynamic_pages_594_products():
    _, snapshot = _snapshot(
        "Mostrando 1-20 de 594 Productos"
    )
    assert snapshot["start"] == 1
    assert snapshot["end"] == 20
    assert snapshot["total"] == 594
    assert snapshot["page_size"] == 20
    assert snapshot["current_page"] == 1
    assert snapshot["total_pages"] == 30


def test_dynamic_pages_change_by_category():
    cases = (
        ("Mostrando 1-20 de 363 Productos", 19),
        ("Mostrando 1-20 de 46 Productos", 3),
        ("Mostrando 1-18 de 18 Productos", 1),
        ("Mostrando 1-20 de 302 Productos", 16),
    )
    for text, expected_pages in cases:
        _, snapshot = _snapshot(text)
        assert snapshot["total_pages"] == expected_pages


def test_last_short_page_keeps_first_page_size():
    scraper = ChedrauiScraper(require_store_context=False)
    page = _FakePage()
    scraper._paginator_next_available = lambda _page: True

    scraper._body_text = (
        lambda _page: "Mostrando 1-20 de 594 Productos"
    )
    first = scraper._pagination_snapshot(page)
    assert first["page_size"] == 20
    assert first["total_pages"] == 30

    scraper._body_text = (
        lambda _page: "Mostrando 581-594 de 594 Productos"
    )
    last = scraper._pagination_snapshot(page)
    assert last["page_size"] == 20
    assert last["current_page"] == 30
    assert last["total_pages"] == 30


def test_total_is_recomputed_if_catalogue_changes():
    scraper = ChedrauiScraper(require_store_context=False)
    page = _FakePage()
    scraper._paginator_next_available = lambda _page: True

    scraper._body_text = (
        lambda _page: "Mostrando 1-20 de 363 Productos"
    )
    before = scraper._pagination_snapshot(page)
    assert before["total"] == 363
    assert before["total_pages"] == 19

    scraper._body_text = (
        lambda _page: "Mostrando 21-40 de 381 Productos"
    )
    after = scraper._pagination_snapshot(page)
    assert after["total"] == 381
    assert after["current_page"] == 2
    assert after["total_pages"] == 20


def test_next_availability_is_part_of_snapshot():
    _, snapshot = _snapshot(
        "Mostrando 581-594 de 594 Productos",
        next_available=False,
    )
    assert snapshot["next_available"] is False


def test_missing_range_does_not_invent_total_pages():
    _, snapshot = _snapshot(
        "Catálogo de desodorantes",
        next_available=False,
    )
    assert snapshot["total"] is None
    assert snapshot["current_page"] is None
    assert snapshot["total_pages"] is None
