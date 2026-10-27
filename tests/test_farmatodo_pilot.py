"""Offline Farmatodo pilot tests: no network or productive file writes."""
from pathlib import Path

import pandas as pd
import pytest

from scraper.config import load_categories
from scraper.retailers.farmatodo import (
    FarmatodoScraper, prices_from_card, promotion_from_card, sku_from_card,
    sku_from_visible_pdp_text, inspect_public_product_page,
    explicit_online_availability, FarmatodoCaptureError
)
from scraper.availability import AVAILABLE, UNKNOWN
from scripts.test_farmatodo import quality, valid_cached, MAX_ATTEMPTS


def test_farmatodo_three_requested_urls_exactly():
    categories = load_categories("config/farmatodo/categories.yaml")
    assert {x.id: x.url for x in categories} == {
        "respiratorios": "https://www.farmatodo.com.mx/medicamentos/respiratorios",
        "cuidado-bucal": "https://www.farmatodo.com.mx/cuidado-personal-y-belleza/cuidado-bucal",
        "higiene-personal": "https://www.farmatodo.com.mx/cuidado-personal-y-belleza/higiene-personal",
    }




def test_nasal_decongestants_subcategory_is_exact_and_distinct():
    """One approved public subcategory URL is NOT complete Respiratorios."""
    from scripts.test_farmatodo import (
        NASAL_DECONGESTANTS_URL, NASAL_TRIAL_ID,
        narrow_to_nasal_decongestants,
    )
    categories = load_categories("config/farmatodo/categories.yaml")
    original = categories[0]
    narrowed = narrow_to_nasal_decongestants(
        original, NASAL_DECONGESTANTS_URL
    )
    assert original.id == "respiratorios"
    assert original.url == (
        "https://www.farmatodo.com.mx/medicamentos/respiratorios"
    )
    assert narrowed.id == NASAL_TRIAL_ID
    assert narrowed.url == NASAL_DECONGESTANTS_URL
    assert narrowed.department == "Medicamentos"
    assert narrowed.name == "Descongestionantes nasales"
    assert narrowed.subcategory == "Respiratorios"
    assert narrowed.sub_subcategory == "Descongestionantes nasales"
    assert {c.id for c in categories} == {
        "respiratorios", "cuidado-bucal", "higiene-personal",
    }
    for invalid in (
        NASAL_DECONGESTANTS_URL + "?page=7",
        NASAL_DECONGESTANTS_URL + "/otro",
        "http://www.farmatodo.com.mx/medicamentos/"
        "respiratorios/descongestionantes-nasales",
        "https://example.com/medicamentos/"
        "respiratorios/descongestionantes-nasales",
        categories[0].url,
    ):
        with pytest.raises(ValueError):
            narrow_to_nasal_decongestants(original, invalid)
    with pytest.raises(ValueError):
        narrow_to_nasal_decongestants(
            categories[1], NASAL_DECONGESTANTS_URL
        )


def test_nasal_trial_excel_and_cache_are_separate_from_main_pilot(
    tmp_path, monkeypatch,
):
    """An isolated subcategory may succeed without changing full retailer."""
    from scripts import test_farmatodo as runner
    standard = tmp_path / "catalogo_online.xlsx"
    standard.write_bytes(b"untouched production pilot")
    monkeypatch.setattr(runner, "OUTPUT", standard)
    subcat_output = (
        tmp_path / "subcategories" / "descongestionantes-nasales.xlsx"
    )
    report = tmp_path / "diagnostics" / "subcat" / "last_run.json"
    row = pd.DataFrame([{
        "retailer": "Farmatodo",
        "category_id": runner.NASAL_TRIAL_ID,
        "scrape_timestamp": "2026-10-27T14:00:00",
        "sku": "7501086453221",
        "url": "https://www.farmatodo.com.mx/item-example/p",
        "sku_source": "pdp_visible_sku",
        "sku_verified": True,
        "price_current": 105.50,
        "postal_code": None, "store": None, "store_id": None,
        "store_context_verified": False,
    }])
    summary = {
        "retailer": "Farmatodo",
        "category_id": runner.NASAL_TRIAL_ID,
        "capture_date": "2026-10-27",
        "status": "COMPLETE", "products": 1,
        "sku_pdp_verified": 1,
    }
    runner.save_excel(
        {runner.NASAL_TRIAL_ID: (row, summary)},
        output_path=subcat_output, report_path=report,
    )
    assert standard.read_bytes() == b"untouched production pilot"
    assert subcat_output.exists()
    assert report.parent.exists()
    assert not report.exists()
    xls = pd.ExcelFile(subcat_output)
    assert set(xls.sheet_names) == {"Concentrado", "Resumen", "SkuAudit"}
    assert runner.NASAL_TRIAL_ID in runner.load_cache(
        "2026-10-27", output_path=subcat_output
    )
    assert runner.load_cache("2026-10-27") == {}


def test_nasal_trial_cli_probe_uses_exact_url_and_isolated_diagnostic(
    monkeypatch, tmp_path,
):
    """Never allow a narrowed trial to impersonate complete Respiratorios."""
    from types import SimpleNamespace
    import sys
    from scripts import test_farmatodo as runner

    url = runner.NASAL_DECONGESTANTS_URL
    calls = []
    class Browser:
        def close(self):
            calls.append("close_browser")
    class Chromium:
        def launch(self, **kw):
            calls.append("launch")
            return Browser()
    class Playwright:
        chromium = Chromium()
    class PlaywrightSession:
        def __enter__(self):
            return Playwright()
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(runner, "sync_playwright", PlaywrightSession)
    monkeypatch.setattr(
        runner, "NASAL_TRIAL_REPORT",
        tmp_path / "subcat" / "last_run.json",
    )
    monkeypatch.setattr(
        runner, "run_category",
        lambda browser, cat, **kwargs: (
            calls.append((cat.id, cat.url, cat.sub_subcategory))
            or [{
                "price_current": 90.0,
                "product": "Spray nasal", "sku_verified": True,
            }],
            {
                "category_id": cat.id,
                "products": 1,
                "status": "PROBE",
                "probe_sku_checked": 1,
                "probe_sku_verified": 1,
                "probe_load_more_verified": True,
            },
        ),
    )
    monkeypatch.setattr(
        runner, "save_excel",
        lambda *a, **kw: pytest.fail("probe should not touch excel"),
    )
    monkeypatch.setattr(
        sys, "argv", [
            "test_farmatodo.py",
            "--category", "respiratorios",
            "--subcategory-url", url,
            "--headed", "--probe",
        ],
    )
    assert runner.main() == 0
    assert (
        runner.NASAL_TRIAL_ID, url, "Descongestionantes nasales"
    ) in calls
    assert "close_browser" in calls
    files = list((tmp_path / "subcat").glob("probe_*.json"))
    assert len(files) == 1
    assert runner.NASAL_TRIAL_ID in files[0].name


def test_farmatodo_offer_prices_and_identifier():
    assert prices_from_card("De $145.00 Para $108.50") == (108.5, 145.0)
    assert prices_from_card("$29.50") == (29.5, 29.5)
    assert prices_from_card("Sin precio") == (None, None)
    sku, source = sku_from_card({
        "url": "https://www.farmatodo.com.mx/colgate-xyz/p",
        "image_url": "https://cdn.example/7509546653518_01.webp",
    })
    assert sku == "7509546653518"
    assert source == "image_ean_candidate"
    assert sku_from_card({"url": "https://www.farmatodo.com.mx/no-id/p"}) == (
        None, "not_found",
    )



def test_image_ean_suffixes_and_invalid_embedded_codes():
    for image in (
        "https://cdn.example/7509546653518_01.webp",
        "https://cdn.example/7509546653518-01.webp",
        "https://cdn.example/7509546653518.webp",
        "https://cdn.example/products/7509546653518_02?width=400",
    ):
        assert sku_from_card({"image_url": image}) == (
            "7509546653518", "image_ean_candidate"
        )
    # Never accept a substring of a longer code or mixed alphanumeric ID.
    for image in (
        "https://cdn.example/175095466535189.webp",
        "https://cdn.example/X7509546653518_01.webp",
        "https://cdn.example/7509546653518X.webp",
    ):
        assert sku_from_card({"image_url": image}) == (
            None, "not_found"
        )





def test_farmatodo_screenshot_card_prices_and_3plus1_promotions():
    """Operator screenshot: five standalone prices plus visual 3+1 badges."""
    cards = [
        ("Levigrix 5 Mg Caja C/30 Tabletas", "$747.50", 747.50),
        ("Xuzal 5 Mg Caja C/20 Tabletas", "$635.00", 635.0),
        ("Dirnelid Suspension Nasal 50 Mcg Frasco", "$465.00", 465.0),
        ("Zyxem Solucion Oral 5 Mg/Ml Frasco Gotero", "$334.00", 334.0),
        ("Zyxem 5 Mg Caja C/10 Tabletas", "$392.50", 392.5),
    ]
    for name, shown_price, value in cards:
        text = name + " 3+1 " + shown_price
        assert prices_from_card(text) == (value, value)
        assert promotion_from_card(text, value, value) == (
            "3+1 (condiciones no verificadas)"
        )
    assert promotion_from_card("Sin promoción $200.00", 200, 200) is None


def test_farmatodo_3plus1_and_monetary_offer_are_independent():
    text = "3 + 1 Colgate De $145.00 Para $108.50"
    current, regular = prices_from_card(text)
    assert (current, regular) == (108.50, 145.0)
    assert promotion_from_card(text, current, regular) == (
        "3+1 (condiciones no verificadas) | Precio promocional"
    )
    assert promotion_from_card("Producto 13+10 $100.00", 100, 100) is None


def test_visible_pdp_sku_is_authoritative_not_product_slug():
    text = (
        "Colgate Pasta Dental Total Prevención Activa Antisarro "
        "Sku: 7509546653518 De $145.00 Por $108.50"
    )
    assert sku_from_visible_pdp_text(text) == "7509546653518"
    assert sku_from_visible_pdp_text(
        "Nombre Colgate 7509546653518 precio $108.50"
    ) is None
    assert sku_from_visible_pdp_text("Sku:") is None
    assert sku_from_visible_pdp_text("SKU: 123456") == "123456"


def test_inspect_pdp_reads_visible_sku_and_rejects_redirects():
    from types import SimpleNamespace
    url = ("https://www.farmatodo.com.mx/"
           "colgate-pasta-dental-total-prevencion-activa-antisarro-c-150-ml/p")
    class Page:
        def __init__(self):
            self.url = ""
            self.text = "Sku: 7509546653518"
            self.reloads = 0
        def goto(self, value, **kwargs):
            self.url = value
        def reload(self, **kwargs):
            self.reloads += 1
        def wait_for_timeout(self, delay):
            pass
        def locator(self, selector):
            assert selector == "body"
            return SimpleNamespace(
                inner_text=lambda **kwargs: self.text
            )
    page = Page()
    result = inspect_public_product_page(page, url)
    assert result["sku"] == "7509546653518"
    assert result["source"] == "pdp_visible_sku"
    assert result["verified"] is True
    assert result["pdp_load_checks"] == 1
    assert result["pdp_reload_count"] == 0
    page.text = "Producto cargando, sin SKU"
    absent = inspect_public_product_page(page, url)
    assert absent["verified"] is False
    assert absent["pdp_load_checks"] == 20
    assert absent["pdp_reload_count"] == 1
    assert absent["pdp_sku_label_present"] is False
    assert page.reloads == 1
    with pytest.raises(FarmatodoCaptureError, match="fuera"):
        inspect_public_product_page(page, "https://example.com/other/p")



def test_oral_b_pdp_sku_can_appear_only_after_reload():
    """Actual Oral-B product exposes Sku: 7501086453221 publicly."""
    from types import SimpleNamespace

    url = ("https://www.farmatodo.com.mx/"
           "oral-b-enjuague-bucal-gingivitis-envase-c-350-ml/p")
    class LateSkuPage:
        def __init__(self):
            self.url = ""
            self.round = 0
            self.checks = 0
        def goto(self, url, **kwargs):
            self.url = url
        def reload(self, **kwargs):
            self.round += 1
        def wait_for_timeout(self, milliseconds):
            pass
        def locator(self, selector):
            assert selector == "body"
            def visible_body(**kwargs):
                self.checks += 1
                return ("Oral B Enjuague Bucal Gingivitis Envase C/350 Ml "
                        "Sku: 7501086453221 $318.00"
                        if self.round else "Cargando producto...")
            return SimpleNamespace(inner_text=visible_body)

    page = LateSkuPage()
    result = inspect_public_product_page(page, url)
    assert result["sku"] == "7501086453221"
    assert result["verified"] is True
    assert result["pdp_load_checks"] == 11
    assert result["pdp_reload_count"] == 1


def test_farmatodo_waits_for_rendered_resp_product_cards(monkeypatch):
    """Zero products at DOMContentLoaded is not a completed/blocked category."""
    from types import SimpleNamespace
    from scraper.retailers import farmatodo as mod
    cat = load_categories("config/farmatodo/categories.yaml")[0]

    class Page:
        url = cat.url
        def __init__(self):
            self.loads = 0
            self.waits = 0
            self.context = SimpleNamespace(
                new_page=lambda: SimpleNamespace(close=lambda: None)
            )
        def goto(self, url, **kwargs):
            self.loads += 1
            self.url = url
        def wait_for_timeout(self, delay):
            self.waits += 1

    page = Page()
    calls = []
    cards = [{
        "url": f"https://www.farmatodo.com.mx/item-{i}/p",
        "product": f"Producto {i}", "text": "$80.00",
    } for i in range(6)]
    def cards_eventually(page):
        calls.append("extract")
        return ([], 6, True) if len(calls) < 5 else (cards, 6, False)
    monkeypatch.setattr(mod, "extract_catalogue", cards_eventually)
    monkeypatch.setattr(mod, "validate_category_page", lambda *args: None)
    monkeypatch.setattr(
        mod, "inspect_public_product_page",
        lambda page, url: {
            "sku": "7501086453221", "source": "pdp_visible_sku",
            "verified": True,
        }
    )
    # In probe, avoid requiring a real page button after initial load by
    # setting has_more=False on the fifth, successfully hydrated read.
    def done_after_load(p):
        cards_now, total, _ = cards_eventually(p)
        return cards_now, total, False
    monkeypatch.setattr(mod, "extract_catalogue", done_after_load)

    rows, meta = FarmatodoScraper(max_rounds=10).capture_category(
        page, cat, probe=True
    )
    assert len(rows) == 6
    assert meta["initial_load_checks"] == 5
    assert meta["initial_reload_count"] == 0
    assert page.loads == 1
    assert meta["pdp_sku_verified"] == 3


def test_farmatodo_reloads_if_initial_catalogue_stays_empty(monkeypatch):
    from types import SimpleNamespace
    from scraper.retailers import farmatodo as mod
    cat = load_categories("config/farmatodo/categories.yaml")[0]

    class Page:
        url = cat.url
        loads = 0
        waits = 0
        def goto(self, url, **kwargs):
            self.loads += 1
            self.url = url
        def wait_for_timeout(self, ms):
            self.waits += 1
        def evaluate(self, source):
            return {"ready_state": "complete", "visible_product_links": 0,
                    "visible_prices": False, "title": "Farmatodo"}

    page = Page()
    monkeypatch.setattr(mod, "validate_category_page", lambda *args: None)
    monkeypatch.setattr(mod, "extract_catalogue",
                        lambda page: ([], 460, False))

    with pytest.raises(FarmatodoCaptureError, match="40 verificaciones"):
        FarmatodoScraper(max_rounds=5).capture_category(page, cat, probe=True)
    assert page.loads == 2
    assert page.waits == 40


def test_probe_checks_three_pdp_product_skus_without_guessing(monkeypatch):
    from types import SimpleNamespace
    from scraper.retailers import farmatodo as mod

    class DetailPage:
        def __init__(self):
            self.url = ""
        def goto(self, url, **kwargs):
            self.url = url
        def wait_for_timeout(self, delay):
            pass
        def locator(self, selector):
            assert selector == "body"
            identifier = self.url.split("/")[-2].split("-")[-1]
            return SimpleNamespace(
                inner_text=lambda **kw: "Sku: " + identifier
            )
        def close(self):
            pass

    class Page:
        url = "https://www.farmatodo.com.mx/cuidado-personal-y-belleza/cuidado-bucal"
        context = SimpleNamespace(new_page=lambda: DetailPage())
        def goto(self, target, **kwargs):
            self.url = target
        def wait_for_timeout(self, timeout):
            pass
        def locator(self, selector):
            return SimpleNamespace(inner_text=lambda **kw: "Farmatodo")

    cards = [
        {
            "url": f"https://www.farmatodo.com.mx/item-{7509546653500 + i}/p",
            "product": "Producto de prueba", "text": "$108.50",
            "image_url": "https://cdn.example/no-ean.webp",
        }
        for i in range(6)
    ]
    cards[0]["promo_badge"] = "3+1"
    monkeypatch.setattr(mod, "extract_catalogue",
                        lambda page: (cards, 6, False))
    cat = load_categories("config/farmatodo/categories.yaml")[1]
    rows, meta = FarmatodoScraper(max_rounds=5).capture_category(
        Page(), cat, probe=True
    )
    assert rows[0]["promotion"] == "3+1 (condiciones no verificadas)"
    assert rows[0]["price_regular"] == 108.5
    assert meta["pdp_sku_checked"] == 3
    assert meta["pdp_sku_verified"] == 3
    assert sum(row["sku_verified"] for row in rows) == 3
    assert all(row["sku_source"] == "pdp_visible_sku" for row in rows[:3])
    assert all(row["sku"] is None for row in rows[3:])
    assert all(row["postal_code"] is None for row in rows)
    assert all(row["store_context_verified"] is False for row in rows)


def test_online_availability_never_inferred_from_price():
    assert explicit_online_availability("Producto $120")[0:2] == (
        UNKNOWN, None
    )
    assert explicit_online_availability("10 disponibles")[0:2] == (
        AVAILABLE, True
    )


def test_farmatodo_quality_requires_coverage_and_sku():
    data = [{
        "url": "https://www.farmatodo.com.mx/item/p",
        "sku": "7509546653518",
        "sku_source": "pdp_visible_sku", "sku_verified": True,
        "price_current": 108.5,
        "postal_code": None,
        "store_context_verified": False,
    }]
    metadata = {
        "category_id": "cuidado-bucal",
        "capture_date": "2026-10-26",
        "target_products": 1,
        "load_more_stalled": False,
        "load_more_remaining": False,
        "sku_source_counts": {"pdp_visible_sku": 1},
        "pdp_sku_checked": 1, "pdp_sku_verified": 1,
    }
    ok = quality(data, metadata)
    assert ok["status"] == "COMPLETE"
    assert ok["store_context_verified"] is False
    assert ok["postal_code"] is None
    assert ok["sku_pdp_verified"] == 1
    assert quality([{**data[0], "sku_verified": False}], metadata)["status"] == "PARTIAL"
    assert quality(data, metadata | {"target_products": 3})["status"] == "PARTIAL"
    incomplete = quality(
        [data[0], {**data[0], "url": "https://www.farmatodo.com.mx/other/p",
                   "sku": None, "sku_verified": False}],
        {**metadata, "target_products": 169},
    )
    assert incomplete["status"] == "PARTIAL"
    assert incomplete["pdp_sku_uninspected"] == 1
    assert "SKU pendientes de inspección PDP: 1/2" in incomplete["quality_notes"]
    assert "SKU no identificado en" not in incomplete["quality_notes"]
    assert "SKU sin evidencia visible en PDP" not in incomplete["quality_notes"]
    assert quality(
        [{**data[0], "sku": None}], metadata
    )["status"] == "PARTIAL"
    assert quality(
        [{**data[0], "postal_code": "11590"}], metadata
    )["status"] == "PARTIAL"


def test_only_same_day_locationless_complete_can_be_reused():
    df = pd.DataFrame([{
        "category_id": "cuidado-bucal",
        "scrape_timestamp": "2026-10-26T10:00:00-06:00",
        "sku": "7509546653518", "price_current": 108.5,
        "url": "https://www.farmatodo.com.mx/colgate/p",
        "postal_code": None, "store_id": None,
        "store_context_verified": False,
    }])
    summary = {
        "capture_date": "2026-10-26",
        "category_id": "cuidado-bucal", "status": "COMPLETE", "products": 1,
        "sku_pdp_verified": 1,
    }
    audit = pd.DataFrame([{
        "category_id": "cuidado-bucal",
        "sku": "7509546653518",
        "url": "https://www.farmatodo.com.mx/colgate/p",
        "sku_verified": True, "sku_source": "pdp_visible_sku",
    }])
    assert valid_cached(df, summary, "2026-10-26", audit) is True
    assert valid_cached(df, summary, "2026-10-27", audit) is False
    assert valid_cached(df, summary, "2026-10-26") is False
    assert valid_cached(
        df.assign(postal_code="11590"), summary, "2026-10-26", audit
    ) is False
    assert valid_cached(
        df, summary, "2026-10-26",
        audit.assign(sku_verified=False),
    ) is False



def test_probe_with_visible_products_but_no_sku_is_not_ready(monkeypatch):
    from types import SimpleNamespace
    from scripts import test_farmatodo as runner

    class DummyPage:
        def close(self):
            pass

    class DummyContext:
        def new_page(self):
            return DummyPage()
        def close(self):
            pass

    class DummyBrowser:
        def new_context(self, **kwargs):
            assert kwargs["locale"] == "es-MX"
            return DummyContext()
        def new_page(self, **kwargs):
            raise AssertionError("browser.new_page must never be called")

    class DummyScraper:
        def __init__(self, max_rounds):
            pass
        def capture_category(self, page, category, probe):
            assert probe is True
            return ([{
                "url": "https://www.farmatodo.com.mx/example/p",
                "sku": None, "price_current": 50, "postal_code": None,
                "store_context_verified": False,
            }], {
                "category_id": "respiratorios", "capture_date": "2026-10-27",
                "target_products": 460, "load_more_remaining": True,
                "sku_debug_samples": [{
                    "url": "https://www.farmatodo.com.mx/example/p",
                    "image_url": "https://img.example/no_ean.webp",
                    "sku_source": "not_found",
                }],
            })

    monkeypatch.setattr(runner, "FarmatodoScraper", DummyScraper)
    _, summary = runner.run_category(
        DummyBrowser(), SimpleNamespace(id="respiratorios"),
        max_rounds=20, probe=True,
    )
    assert summary["status"] == "NEEDS_SKU_REVIEW"
    assert summary["probe_sku_verified"] == 0
    assert summary["sku_debug_samples"][0]["sku_source"] == "not_found"



def test_farmatodo_auto_browser_creates_and_closes_one_context_per_attempt(monkeypatch):
    from types import SimpleNamespace
    from scripts import test_farmatodo as runner

    events = []

    class FakePage:
        def close(self):
            events.append("page_closed")

    class FakeContext:
        def new_page(self):
            events.append("context.new_page")
            return FakePage()
        def close(self):
            events.append("context_closed")

    class AutoBrowser:
        def new_page(self, **kwargs):
            raise AssertionError("browser.new_page is forbidden")
        def new_context(self, **kwargs):
            assert kwargs == {
                "locale": "es-MX",
                "viewport": {"width": 1440, "height": 1000},
            }
            events.append("browser.new_context")
            return FakeContext()

    class EmptyProbe:
        def __init__(self, **kwargs):
            pass
        def capture_category(self, page, category, probe):
            assert probe is True
            return [], {
                "category_id": category.id,
                "capture_date": "2026-10-27",
                "target_products": 460,
            }

    monkeypatch.setattr(runner, "FarmatodoScraper", EmptyProbe)
    _, summary = runner.run_category(
        AutoBrowser(), SimpleNamespace(id="respiratorios"),
        max_rounds=100, probe=True,
    )
    assert summary["status"] == "NEEDS_SKU_REVIEW"
    assert events == [
        "browser.new_context", "context.new_page",
        "page_closed", "context_closed",
    ]


def test_farmatodo_auto_context_cleanup_after_three_failures(monkeypatch):
    from types import SimpleNamespace
    from scripts import test_farmatodo as runner

    events = []

    class Context:
        def new_page(self):
            events.append("open_page")
            return SimpleNamespace(close=lambda: events.append("close_page"))
        def close(self):
            events.append("close_context")

    class Browser:
        def new_context(self, **kwargs):
            events.append("open_context")
            return Context()
        def new_page(self, **kwargs):
            raise AssertionError("Do not use browser.new_page")

    class FailingScraper:
        def __init__(self, **kwargs):
            pass
        def capture_category(self, page, category, probe):
            raise RuntimeError("simulated failure")

    monkeypatch.setattr(runner, "FarmatodoScraper", FailingScraper)
    monkeypatch.setattr(runner.time, "sleep", lambda duration: None)
    rows, summary = runner.run_category(
        Browser(), SimpleNamespace(id="respiratorios"),
        max_rounds=100, probe=False,
    )
    assert rows == []
    assert summary["status"] == "PARTIAL"
    assert summary["attempts"] == 3
    assert events.count("open_context") == 3
    assert events.count("open_page") == 3
    assert events.count("close_page") == 3
    assert events.count("close_context") == 3


def test_farmatodo_cdp_reuses_existing_context_without_closing(monkeypatch):
    from types import SimpleNamespace
    from scripts import test_farmatodo as runner

    events = []

    class ExistingContext:
        def new_page(self):
            events.append("page_opened")
            return SimpleNamespace(close=lambda: events.append("page_closed"))
        def close(self):
            raise AssertionError("Operator Chrome context must stay open")

    class Browser:
        contexts = [ExistingContext()]
        def new_context(self, **kwargs):
            raise AssertionError("CDP session should reuse original context")
        def new_page(self, **kwargs):
            raise AssertionError("Never use browser.new_page")

    class EmptyProbe:
        def __init__(self, **kwargs):
            pass
        def capture_category(self, page, category, probe):
            return [], {
                "category_id": category.id,
                "capture_date": "2026-10-27",
                "target_products": 169,
            }

    monkeypatch.setattr(runner, "FarmatodoScraper", EmptyProbe)
    _, result = runner.run_category(
        Browser(), SimpleNamespace(id="cuidado-bucal"),
        max_rounds=100, probe=True, existing_context=True,
    )
    assert result["status"] == "NEEDS_SKU_REVIEW"
    assert events == ["page_opened", "page_closed"]


def test_farmatodo_pilot_writes_pdp_audit_in_isolated_excel(tmp_path, monkeypatch):
    from scripts import test_farmatodo as runner
    output = tmp_path / "farmatodo.xlsx"
    report = tmp_path / "farmatodo-reports" / "last_run.json"
    monkeypatch.setattr(runner, "OUTPUT", output)
    monkeypatch.setattr(runner, "REPORT", report)
    item = pd.DataFrame([{
        "category_id": "cuidado-bucal",
        "scrape_timestamp": "2026-10-27T10:00:00",
        "sku": "7509546653518",
        "url": "https://www.farmatodo.com.mx/colgate/p",
        "sku_source": "pdp_visible_sku", "sku_verified": True,
        "price_current": 108.5,
        "postal_code": None, "store_id": None,
        "store_context_verified": False,
    }])
    summary = {
        "retailer": "Farmatodo", "category_id": "cuidado-bucal",
        "capture_date": "2026-10-27", "status": "COMPLETE",
        "products": 1, "sku_pdp_verified": 1,
    }
    runner.save_excel({"cuidado-bucal": (item, summary)})
    assert output.exists()
    audit = pd.read_excel(output, sheet_name="SkuAudit", dtype={"sku": str})
    assert audit["sku"].iloc[0] == "7509546653518"
    assert bool(audit["sku_verified"].iloc[0]) is True
    assert audit["sku_source"].iloc[0] == "pdp_visible_sku"
    reused = runner.load_cache("2026-10-27")
    assert "cuidado-bucal" in reused
    assert reused["cuidado-bucal"][0]["sku_verified"].iloc[0] == True



def test_probe_clicks_mostrar_mas_and_detects_new_cards(monkeypatch):
    from types import SimpleNamespace
    from scraper.retailers import farmatodo as mod

    class DetailPage:
        url = ""
        def goto(self, url, **kwargs):
            self.url = url
        def wait_for_timeout(self, ms):
            pass
        def locator(self, selector):
            return SimpleNamespace(
                inner_text=lambda **kw: "Sku: " +
                self.url.split("/")[-2].split("-")[-1]
            )
        def close(self):
            pass

    class Button:
        def __init__(self, page):
            self.page = page
        def is_visible(self):
            return True
        def scroll_into_view_if_needed(self, **kw):
            pass
        def click(self, **kw):
            self.page.clicked = True

    class Page:
        url = "https://www.farmatodo.com.mx/cuidado-personal-y-belleza/cuidado-bucal"
        context = SimpleNamespace(new_page=lambda: DetailPage())
        clicked = False
        def goto(self, url, **kw):
            self.url = url
        def wait_for_timeout(self, ms):
            pass
        def locator(self, selector):
            return SimpleNamespace(inner_text=lambda **kw: "Farmatodo")
        def get_by_text(self, pattern):
            return SimpleNamespace(count=lambda: 1, last=Button(self))
    def make_cards(size):
        return [{
            "url": f"https://www.farmatodo.com.mx/item-{7509546653500+i}/p",
            "product": f"Test {i}", "text": "$108.50",
        } for i in range(size)]

    def read_listing(page):
        if getattr(page, "force_timeout", False):
            return make_cards(6), 169, True
        return (make_cards(12) if page.clicked else make_cards(6)), 169, True

    monkeypatch.setattr(mod, "extract_catalogue", read_listing)
    page = Page()
    cat = load_categories("config/farmatodo/categories.yaml")[1]
    rows, meta = FarmatodoScraper(max_rounds=100).capture_category(
        page, cat, probe=True
    )
    assert page.clicked is True
    assert len(rows) == 12
    assert meta["load_more_clicks"] == 1
    assert meta["probe_load_more_verified"] is True
    assert meta["load_more_stop_reason"] == "probe_one_click_verified"
    assert meta["pdp_sku_verified"] == 3

    timed_out = Page()
    timed_out.force_timeout = True
    failed_rows, failed_meta = FarmatodoScraper(max_rounds=100).capture_category(
        timed_out, cat, probe=True
    )
    assert len(failed_rows) == 6
    assert failed_meta["load_more_stalled"] is True
    assert failed_meta["load_more_error"] == "click_added_no_priced_products"
    assert failed_meta["load_more_stop_reason"] == "click_added_no_priced_products"
    assert failed_meta["probe_load_more_verified"] is False
    assert failed_meta["pdp_sku_verified"] == 3



def test_full_capture_survives_temporarily_disappearing_mostrar_mas(monkeypatch):
    """A transient has_more=False after a click must NOT finish capture."""
    from types import SimpleNamespace
    from scraper.retailers import farmatodo as mod
    cat = load_categories("config/farmatodo/categories.yaml")[0]

    class Button:
        def __init__(self, page):
            self.page = page
        def is_visible(self):
            return True
        def is_enabled(self):
            return True
        def scroll_into_view_if_needed(self, **kw):
            pass
        def click(self, **kw):
            self.page.clicks += 1

    class Page:
        url = cat.url
        def __init__(self):
            self.clicks = 0
            self.button_absent_checks = 0
            self.context = SimpleNamespace(
                new_page=lambda: SimpleNamespace(close=lambda: None)
            )
        def goto(self, url, **kw):
            self.url = url
        def wait_for_timeout(self, ms):
            pass
        def get_by_text(self, regex):
            def count():
                if self.clicks == 1 and self.button_absent_checks < 2:
                    self.button_absent_checks += 1
                    return 0
                return 1
            return SimpleNamespace(count=count, last=Button(self))

    def listing(page):
        count = min(6 + 5 * page.clicks, 16)
        cards = [{
            "url": f"https://www.farmatodo.com.mx/product-{i}/p",
            "product": f"Producto {i}", "text": "$50.00",
        } for i in range(count)]
        # False after click 1 though 5 remain; TRUE at published
        # target after click 2 (stale visible button during SPA update).
        return cards, 16, page.clicks != 1

    monkeypatch.setattr(mod, "validate_category_page", lambda *args: None)
    monkeypatch.setattr(mod, "extract_catalogue", listing)
    monkeypatch.setattr(
        mod, "inspect_public_product_page",
        lambda page, url: {
            "sku": url.rstrip("/").split("/")[-2].replace("product-", "SKU-"),
            "source": "pdp_visible_sku", "verified": True,
        },
    )
    page = Page()
    rows, meta = FarmatodoScraper(max_rounds=10).capture_category(
        page, cat, probe=False
    )
    assert page.clicks == 2
    assert page.button_absent_checks == 2
    assert len(rows) == 16
    assert meta["load_more_clicks"] == 2
    assert meta["pdp_sku_checked"] == 16
    assert meta["pdp_sku_verified"] == 16
    assert meta["load_more_stalled"] is False
    assert meta["load_more_stop_reason"] == "published_target_reached"
    assert quality(rows, meta)["status"] == "COMPLETE"


def test_full_capture_rejects_missing_button_before_target(monkeypatch):
    """When the control never returns, fail closed with a clear reason."""
    from types import SimpleNamespace
    from scraper.retailers import farmatodo as mod
    cat = load_categories("config/farmatodo/categories.yaml")[1]
    cards = [{
        "url": f"https://www.farmatodo.com.mx/product-{i}/p",
        "product": f"Producto {i}", "text": "$100.00",
    } for i in range(6)]

    class Page:
        url = cat.url
        def __init__(self):
            self.waits = 0
            self.context = SimpleNamespace(
                new_page=lambda: SimpleNamespace(close=lambda: None)
            )
        def goto(self, url, **kw):
            self.url = url
        def wait_for_timeout(self, ms):
            self.waits += 1
        def get_by_text(self, regex):
            return SimpleNamespace(count=lambda: 0)

    monkeypatch.setattr(mod, "validate_category_page", lambda *args: None)
    monkeypatch.setattr(
        mod, "extract_catalogue", lambda page: (cards, 169, False)
    )
    monkeypatch.setattr(
        mod, "inspect_public_product_page",
        lambda page, url: {
            "sku": "7509546653518", "source": "pdp_visible_sku",
            "verified": True,
        },
    )
    page = Page()
    rows, meta = FarmatodoScraper(max_rounds=100).capture_category(
        page, cat, probe=False
    )
    assert len(rows) == 6
    assert page.waits == 16
    assert meta["load_more_stalled"] is True
    assert meta["load_more_clicks"] == 0
    assert meta["load_more_error"] == (
        "button_not_visible_or_enabled_before_target"
    )
    assert meta["load_more_stop_reason"] == meta["load_more_error"]
    assert meta["pdp_sku_checked"] == 3
    assert quality(rows, meta)["status"] == "PARTIAL"



def test_public_pagination_dom_evidence_is_bounded_and_safe():
    """Inspect PUBLIC DOM navigation without reading network/cookies."""
    from scraper.retailers.farmatodo import inspect_public_pagination_ui

    category_url = ("https://www.farmatodo.com.mx/"
                    "medicamentos/respiratorios")
    class Page:
        def evaluate(self, source, url):
            assert url == category_url
            assert "document.querySelectorAll" in source
            assert "public_subcategory_links" in source
            assert "visible_pagination_links" in source
            assert "cookies" not in source.lower()
            assert "fetch(" not in source
            return {
                "url": url,
                "public_subcategory_links": [
                    {"label": "Antitusivos", "url": url + "/antitusivos"},
                ],
                "visible_pagination_links": [],
                "visible_buttons": [],
            }

    found = inspect_public_pagination_ui(Page(), category_url)
    assert found["public_subcategory_links"][0]["label"] == "Antitusivos"
    assert found["visible_pagination_links"] == []

    class MissingEvaluate:
        pass
    assert inspect_public_pagination_ui(
        MissingEvaluate(), category_url
    ) == {"diagnostic_unavailable": "AttributeError"}


def test_partial_retries_preserve_best_coverage_not_last_attempt(monkeypatch):
    """99/460, 96/460, 96/460 should retain 99 in non-integrable pilot."""
    from types import SimpleNamespace
    from scripts import test_farmatodo as runner

    class Page:
        def close(self):
            pass

    class Context:
        def new_page(self):
            return Page()
        def close(self):
            pass

    class Browser:
        def new_context(self, **kwargs):
            return Context()

    attempts = []
    class Incomplete:
        def __init__(self, **kw):
            pass
        def capture_category(self, page, category, probe):
            n = (99, 96, 96)[len(attempts)]
            attempts.append(n)
            rows = [{
                "url": f"https://www.farmatodo.com.mx/item-{i}/p",
                "sku": None, "price_current": 50.0,
                "postal_code": None, "store_context_verified": False,
            } for i in range(n)]
            return rows, {
                "category_id": "respiratorios",
                "capture_date": "2026-10-27",
                "target_products": 460,
                "load_more_stalled": True,
                "load_more_remaining": True,
                "load_more_stop_reason":
                    "button_not_visible_or_enabled_before_target",
                "pdp_sku_checked": 0,
                "pdp_sku_verified": 0,
                "load_more_ui_debug": {
                    "url": "https://www.farmatodo.com.mx/medicamentos/respiratorios",
                    "public_subcategory_links": [], "visible_pagination_links": [],
                },
            }

    monkeypatch.setattr(runner, "FarmatodoScraper", Incomplete)
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    rows, summary = runner.run_category(
        Browser(), SimpleNamespace(id="respiratorios"),
        max_rounds=100, probe=False,
    )
    assert attempts == [99, 96, 96]
    assert len(rows) == 99
    assert summary["products"] == 99
    assert summary["best_attempt"] == 1
    assert summary["attempts"] == 3
    assert summary["status"] == "PARTIAL"
    assert summary["load_more_stop_reason"] == (
        "button_not_visible_or_enabled_before_target"
    )

    # Explicit fast diagnostic may run one attempt; productive pipeline
    # still defaults to three attempts when --attempts is omitted.
    attempts.clear()
    one_rows, one_summary = runner.run_category(
        Browser(), SimpleNamespace(id="respiratorios"),
        max_rounds=100, probe=False, max_attempts=1,
    )
    assert attempts == [99]
    assert len(one_rows) == 99
    assert one_summary["attempts"] == 1
    assert one_summary["best_attempt"] == 1



def test_farmatodo_next_page_only_from_real_observed_public_route():
    from scraper.retailers.farmatodo import next_observed_public_page_url

    category = "https://www.farmatodo.com.mx/medicamentos/respiratorios"
    assert next_observed_public_page_url(
        category + "?page=6", category,
    ) == category + "?page=7"
    assert next_observed_public_page_url(
        category + "?page=6&order=asc", category,
    ) == category + "?page=7&order=asc"

    # Not allowed to INVENT the page parameter or move to another route.
    for url in (
        category, category + "?page=1", category + "?page=foo",
        category + "?page=6&page=7",
        "https://evil.example/medicamentos/respiratorios?page=6",
        "https://www.farmatodo.com.mx/medicamentos/otro?page=6",
        category + "?page=200",
    ):
        assert next_observed_public_page_url(url, category) is None


def test_farmatodo_observed_page_7_must_render_new_priced_cards(monkeypatch):
    from scraper.retailers import farmatodo as mod
    from scraper.retailers.farmatodo import visit_next_observed_public_page
    cat = load_categories("config/farmatodo/categories.yaml")[0]
    current = cat.url + "?page=6"
    original_card = {
        "url": "https://www.farmatodo.com.mx/item-0/p",
        "product": "Producto 0", "text": "$100.00",
    }
    next_card = {
        "url": "https://www.farmatodo.com.mx/item-1/p",
        "product": "Producto 1", "text": "$120.00",
    }

    class Page:
        def __init__(self):
            self.url = current
            self.calls = []
        def goto(self, url, **kwargs):
            self.url = url
            self.calls.append(url)
        def wait_for_timeout(self, delay):
            pass

    page = Page()
    monkeypatch.setattr(mod, "validate_category_page", lambda *args: None)
    monkeypatch.setattr(mod, "extract_catalogue", lambda page: (
        [original_card, next_card], 460, False,
    ))
    result = visit_next_observed_public_page(
        page, cat, {original_card["url"]: original_card}, 460,
    )
    assert page.calls == [cat.url + "?page=7"]
    assert result["status"] == "validated_new_products"
    assert result["new_products"] == 1
    assert result["candidate_page"] == 7
    assert result["published_total"] == 460


def test_farmatodo_page_7_without_new_products_never_certified(monkeypatch):
    from scraper.retailers import farmatodo as mod
    from scraper.retailers.farmatodo import visit_next_observed_public_page
    cat = load_categories("config/farmatodo/categories.yaml")[0]
    card = {
        "url": "https://www.farmatodo.com.mx/item-0/p",
        "product": "Product 0", "text": "$80.00",
    }

    class Page:
        url = cat.url + "?page=6"
        def __init__(self):
            self.waits = 0
        def goto(self, url, **kwargs):
            self.url = url
        def wait_for_timeout(self, delay):
            self.waits += 1

    page = Page()
    monkeypatch.setattr(mod, "validate_category_page", lambda *args: None)
    monkeypatch.setattr(mod, "extract_catalogue",
                        lambda page: ([card], 460, False))
    result = visit_next_observed_public_page(
        page, cat, {card["url"]: card}, 460,
    )
    assert result["status"] == "no_new_priced_products"
    assert result["new_products"] == 0
    assert result["checks"] == 20
    assert page.waits == 19


def test_farmatodo_real_observed_page_6_fallback_finishes_verified_capture(
    monkeypatch,
):
    from types import SimpleNamespace
    from scraper.retailers import farmatodo as mod
    cat = load_categories("config/farmatodo/categories.yaml")[0]

    class Button:
        def __init__(self, page):
            self.page = page
        def is_visible(self):
            return True
        def is_enabled(self):
            return True
        def scroll_into_view_if_needed(self, **kw):
            pass
        def click(self, **kw):
            self.page.clicks += 1
            # Observed site navigation following Mostrar más.
            self.page.url = cat.url + "?page=6"

    class Page:
        url = cat.url
        def __init__(self):
            self.clicks = 0
            self.navigations = []
            self.context = SimpleNamespace(
                new_page=lambda: SimpleNamespace(close=lambda: None)
            )
        def goto(self, url, **kwargs):
            self.url = url
            self.navigations.append(url)
        def wait_for_timeout(self, ms):
            pass
        def get_by_text(self, pattern):
            return SimpleNamespace(
                count=lambda: 1 if self.clicks == 0 else 0,
                last=Button(self),
            )

    def listing(page):
        if page.url.endswith("?page=7"):
            n = 12
        elif page.clicks:
            n = 8
        else:
            n = 6
        return ([{
            "url": f"https://www.farmatodo.com.mx/product-{i}/p",
            "product": f"Producto {i}", "text": "$75.00",
        } for i in range(n)], 12, page.clicks == 0)

    monkeypatch.setattr(mod, "validate_category_page", lambda *args: None)
    monkeypatch.setattr(mod, "extract_catalogue", listing)
    monkeypatch.setattr(mod, "inspect_public_product_page",
        lambda page, url: {
            "sku": url.rstrip("/").split("/")[-2],
            "source": "pdp_visible_sku", "verified": True,
        },
    )
    page = Page()
    rows, meta = FarmatodoScraper(max_rounds=10).capture_category(
        page, cat, probe=False,
    )
    assert len(rows) == 12
    assert page.clicks == 1
    assert cat.url + "?page=7" in page.navigations
    assert meta["load_more_clicks"] == 1
    assert meta["pagination_rounds"] == 2
    assert meta["pdp_sku_verified"] == 12
    assert meta["load_more_stalled"] is False
    assert meta["load_more_stop_reason"] == "published_target_reached"
    assert meta["public_page_fallbacks"][0]["status"] == "validated_new_products"
    certified = quality(rows, meta)
    assert certified["status"] == "COMPLETE"
    assert certified["pagination_rounds"] == 2
    assert certified["last_catalogue_url"].endswith("?page=7")
    import json
    audited_fallbacks = json.loads(certified["public_page_fallbacks"])
    assert len(audited_fallbacks) == 1
    assert audited_fallbacks[0]["candidate_page"] == 7
    assert audited_fallbacks[0]["new_products"] == 4


def test_farmatodo_click_error_is_recovered_when_new_products_arrived(
    monkeypatch,
):
    from types import SimpleNamespace
    from scraper.retailers import farmatodo as mod
    cat = load_categories("config/farmatodo/categories.yaml")[1]

    class Button:
        def __init__(self, page):
            self.page = page
        def is_visible(self):
            return True
        def is_enabled(self):
            return True
        def scroll_into_view_if_needed(self, **kw):
            pass
        def click(self, **kw):
            self.page.clicked = True
            raise RuntimeError("DOM detached during public navigation")

    class Page:
        url = cat.url
        def __init__(self):
            self.clicked = False
            self.context = SimpleNamespace(
                new_page=lambda: SimpleNamespace(close=lambda: None)
            )
        def goto(self, url, **kw):
            self.url = url
        def wait_for_timeout(self, ms):
            pass
        def get_by_text(self, regexp):
            return SimpleNamespace(count=lambda: 1, last=Button(self))

    def listing(page):
        n = 12 if page.clicked else 6
        return ([{
            "url": f"https://www.farmatodo.com.mx/item-{i}/p",
            "product": f"Producto {i}", "text": "$60.00",
        } for i in range(n)], 12, not page.clicked)

    monkeypatch.setattr(mod, "validate_category_page", lambda *args: None)
    monkeypatch.setattr(mod, "extract_catalogue", listing)
    monkeypatch.setattr(mod, "inspect_public_product_page",
        lambda page, url: {
            "sku": url.rstrip("/").split("/")[-2],
            "source": "pdp_visible_sku", "verified": True,
        },
    )
    rows, meta = FarmatodoScraper(max_rounds=5).capture_category(
        Page(), cat, probe=False,
    )
    assert len(rows) == 12
    assert meta["pagination_rounds"] == 1
    assert meta["load_more_stalled"] is False
    assert meta["pdp_sku_checked"] == 12
    assert quality(rows, meta)["status"] == "COMPLETE"



def test_probe_can_report_ready_after_three_verified_pdp_skus(monkeypatch):
    from types import SimpleNamespace
    from scripts import test_farmatodo as runner

    class DummyPage:
        def close(self):
            pass

    class DummyContext:
        def new_page(self):
            return DummyPage()
        def close(self):
            pass

    class DummyBrowser:
        def new_context(self, **kwargs):
            assert kwargs["locale"] == "es-MX"
            return DummyContext()
        def new_page(self, **kwargs):
            raise AssertionError("browser.new_page must never be called")

    class ReadyScraper:
        def __init__(self, **kwargs):
            pass
        def capture_category(self, page, category, probe):
            rows = [
                {
                    "url": f"https://www.farmatodo.com.mx/example-{i}/p",
                    "sku": str(7509546653500 + i) if i < 3 else None,
                    "sku_verified": i < 3,
                    "sku_source": "pdp_visible_sku" if i < 3
                                  else "pdp_not_inspected",
                    "price_current": 108.5, "postal_code": None,
                    "store_context_verified": False,
                }
                for i in range(6)
            ]
            return rows, {
                "category_id": "cuidado-bucal", "capture_date": "2026-10-27",
                "target_products": 169, "load_more_remaining": True,
                "pdp_sku_checked": 3, "pdp_sku_verified": 3,
                "probe_load_more_verified": True,
            }

    monkeypatch.setattr(runner, "FarmatodoScraper", ReadyScraper)
    rows, summary = runner.run_category(
        DummyBrowser(), SimpleNamespace(id="cuidado-bucal"),
        max_rounds=100, probe=True,
    )
    assert len(rows) == 6
    assert summary["status"] == "PROBE"
    assert summary["probe_sku_verified"] == 3
    assert summary["probe_sku_checked"] == 3
    assert summary["probe_load_more_verified"] is True
    assert "muestra 6/169" in summary["quality_notes"]
    assert "SKU verificado 3/3" in summary["quality_notes"]
    assert "SKU no identificado en" not in summary["quality_notes"]

    class MissingPagination(ReadyScraper):
        def capture_category(self, page, category, probe):
            data, meta = super().capture_category(page, category, probe)
            meta["probe_load_more_verified"] = False
            return data, meta

    monkeypatch.setattr(runner, "FarmatodoScraper", MissingPagination)
    _, no_pagination = runner.run_category(
        DummyBrowser(), SimpleNamespace(id="cuidado-bucal"),
        max_rounds=100, probe=True,
    )
    assert no_pagination["probe_sku_verified"] == 3
    assert no_pagination["status"] == "NEEDS_PAGINATION_REVIEW"


def test_pilot_does_not_write_productive_master_or_ask_for_postcode():
    text = Path("scripts/test_farmatodo.py").read_text(encoding="utf-8")
    assert "MAX_ATTEMPTS = 3" in text
    assert "integrate_complete_retailers" not in text
    assert "current_master.json" not in text
    assert "postal_code" in text
    assert "pilots" in text
