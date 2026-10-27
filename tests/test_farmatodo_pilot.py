"""Offline Farmatodo pilot tests: no network or productive file writes."""
from pathlib import Path

import pandas as pd

from scraper.config import load_categories
from scraper.retailers.farmatodo import (
    FarmatodoScraper, prices_from_card, sku_from_card,
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
        def goto(self, value, **kwargs):
            self.url = value
        def wait_for_timeout(self, delay):
            pass
        def locator(self, selector):
            assert selector == "body"
            return SimpleNamespace(
                inner_text=lambda **kwargs: self.text
            )
    page = Page()
    result = inspect_public_product_page(page, url)
    assert result == {
        "sku": "7509546653518",
        "source": "pdp_visible_sku", "verified": True,
    }
    page.text = "Producto cargando, sin SKU"
    assert inspect_public_product_page(page, url)["verified"] is False
    import pytest
    with pytest.raises(FarmatodoCaptureError, match="fuera"):
        inspect_public_product_page(page, "https://example.com/other/p")


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
    monkeypatch.setattr(mod, "extract_catalogue",
                        lambda page: (cards, 169, True))
    cat = load_categories("config/farmatodo/categories.yaml")[1]
    rows, meta = FarmatodoScraper(max_rounds=5).capture_category(
        Page(), cat, probe=True
    )
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

    class DummyBrowser:
        def new_page(self, **kwargs):
            return DummyPage()

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


def test_probe_can_report_ready_after_three_verified_pdp_skus(monkeypatch):
    from types import SimpleNamespace
    from scripts import test_farmatodo as runner

    class DummyPage:
        def close(self):
            pass

    class DummyBrowser:
        def new_page(self, **kwargs):
            return DummyPage()

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


def test_pilot_does_not_write_productive_master_or_ask_for_postcode():
    text = Path("scripts/test_farmatodo.py").read_text(encoding="utf-8")
    assert "MAX_ATTEMPTS = 3" in text
    assert "integrate_complete_retailers" not in text
    assert "current_master.json" not in text
    assert "postal_code" in text
    assert "pilots" in text
