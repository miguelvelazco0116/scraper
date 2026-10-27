"""Offline Phase 7 tests: no live network, no master writes."""
from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from scraper.retailers import mercadolibre as ml
from scripts import test_mercadolibre as pilot


def search_html(*, first=True, total=3, disabled=False, duplicate=False):
    item_id = "MLM-123456789" if first or duplicate else "MLM-987654321"
    label = "Colgate Pasta Dental" if first or duplicate else "Oral B Enjuague"
    price = "1,299" if first or duplicate else "150"
    previous = """
      <div class="poly-price__original">
        <span class="andes-money-amount">
          <span class="andes-money-amount__fraction">1,450</span>
        </span>
      </div>
    """ if first else ""
    next_button = (
        '<li class="andes-pagination__button--next andes-pagination__button--disabled">'
        "Siguiente</li>"
        if disabled else
        '<li class="andes-pagination__button--next">'
        '<a href="https://listado.mercadolibre.com.mx/pasta-dental_Desde_51">'
        "Siguiente</a></li>"
    )
    return f"""
    <html><head><title>Mercado Libre MX</title></head><body>
      <span class="ui-search-search-result__quantity-results">
        {total} resultados
      </span>
      <ol class="ui-search-layout">
        <li class="ui-search-layout__item">
          <a class="poly-component__title"
             href="https://articulo.mercadolibre.com.mx/{item_id}-producto?tracking=123">
            <h2 class="poly-component__title">{label}</h2>
          </a>
          {previous}
          <div class="poly-price__current">
            <span class="andes-money-amount">
              <span class="andes-money-amount__fraction">{price}</span>
              <span class="andes-money-amount__cents">50</span>
            </span>
          </div>
          <span class="poly-price__discount">10% OFF</span>
        </li>
      </ol>
      <ul>{next_button}</ul>
    </body></html>
    """


def test_marketplace_price_mxn_and_no_ars_assumption():
    assert ml.parse_mxn("$1,299.50") == 1299.50
    assert ml.parse_mxn("50") == 50.0
    assert ml.parse_mxn("0") is None
    assert ml.parse_mxn("no-price") is None


def test_publication_id_and_catalog_id_are_distinct_from_sku():
    assert ml.listing_identity(
        "https://articulo.mercadolibre.com.mx/MLM-123456789-colgate"
    ) == ("MLM123456789", None)
    assert ml.listing_identity(
        "https://www.mercadolibre.com.mx/p/MLM123456789"
    ) == (None, "MLM123456789")
    assert ml.listing_identity(
        "https://www.mercadolibre.com.mx/some-product"
    ) == (None, None)


def test_search_page_parses_current_regular_promo_and_next_link():
    page = ml.parse_search_page(
        search_html(), "https://listado.mercadolibre.com.mx/pasta-dental"
    )
    assert page.reported_total == 3
    assert len(page.listings) == 1
    assert page.next_url.endswith("_Desde_51")
    item = page.listings[0]
    assert item["publication_id"] == "MLM123456789"
    assert item["catalog_product_id"] is None
    assert item["price_current"] == 1299.50
    assert item["price_regular"] == 1450.00
    assert item["promotion"] == "10% OFF"
    assert item["currency"] == "MXN"
    assert item["url"].endswith("MLM-123456789-producto")
    assert item["seller"] is None
    assert item["sponsored"] is False


def test_public_page_has_explicit_terminal_marker():
    page = ml.parse_search_page(
        search_html(first=False, total=1, disabled=True),
        "https://listado.mercadolibre.com.mx/pasta-dental",
    )
    assert page.next_url is None
    assert page.explicit_end is True
    assert page.listings[0]["price_current"] == 150.50
    assert page.listings[0]["price_regular"] is None


def test_listing_rejects_untrusted_domains_and_pagination():
    assert ml.clean_link(
        "https://evil.example/MLM-123456789", ml.BASE_URL
    ) is None
    with pytest.raises(ml.MercadoLibreCaptureError):
        ml.parse_search_page(
            search_html().replace(
                "https://listado.mercadolibre.com.mx/pasta-dental_Desde_51",
                "https://evil.example/siguiente"
            ), "https://listado.mercadolibre.com.mx/pasta-dental"
        )


class StubSession:
    def __init__(self, html_pages):
        self.html_pages = html_pages
        self.calls = []
    def get(self, url, timeout):
        self.calls.append((url, timeout))
        response = self.html_pages.pop(0)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(
            url=url,
            text=response,
            raise_for_status=lambda: None,
        )


def test_run_search_deduplicates_and_reports_partial_when_total_missing(
    monkeypatch,
):
    session = StubSession([
        search_html(first=True, total=3),
        search_html(first=False, total=3, disabled=True),
    ])
    monkeypatch.setattr(ml.time, "sleep", lambda ms: None)
    rows, report = ml.run_search(
        "pasta dental", max_pages=5, max_results=100,
        session=session, delay_seconds=1.0,
    )
    assert len(rows) == 2
    assert rows[0]["publication_id"] != rows[1]["publication_id"]
    assert report["status"] == "PARTIAL"
    assert report["reason"] == "listed_count_differs_from_total"
    assert report["reported_total"] == 3
    assert report["pages_checked"] == 2
    assert all(timeout == 30 for _, timeout in session.calls)


def test_run_search_two_pages_exact_total_is_complete(monkeypatch):
    session = StubSession([
        search_html(first=True, total=2),
        search_html(first=False, total=2, disabled=True),
    ])
    monkeypatch.setattr(ml.time, "sleep", lambda ms: None)
    rows, report = ml.run_search(
        "pasta dental", max_pages=3, max_results=100,
        session=session, delay_seconds=1.0,
    )
    assert len(rows) == 2
    assert report["status"] == "COMPLETE"
    assert report["reason"] == "verified_last_page"


def test_run_search_never_counts_duplicate_as_new(monkeypatch):
    session = StubSession([
        search_html(first=True, total=2),
        search_html(first=False, total=2, disabled=True, duplicate=True),
    ])
    monkeypatch.setattr(ml.time, "sleep", lambda ms: None)
    rows, report = ml.run_search(
        "pasta dental", max_pages=3,
        session=session, delay_seconds=1.0,
    )
    assert len(rows) == 1
    assert report["status"] == "PARTIAL"
    assert report["reason"] == "no_new_products"


def test_run_search_max_results_never_inflates_coverage():
    session = StubSession([search_html(total=99)])
    rows, report = ml.run_search(
        "pasta dental", max_pages=10, max_results=1,
        session=session,
    )
    assert len(rows) == 1
    assert report["reason"] == "user_limit_reached"
    assert report["status"] == "PARTIAL"


def test_run_search_fails_closed_on_http_error():
    session = StubSession([
        ml.requests.HTTPError("403 blocked")
    ])
    rows, report = ml.run_search(
        "pasta dental", max_pages=1, session=session,
    )
    assert rows == []
    assert report["status"] == "PARTIAL"
    assert report["reason"] == "http_or_page_error"
    assert report["pages"][0]["error_type"] == "HTTPError"


def test_output_schema_preserves_publication_ids_and_no_sku():
    query = "pasta dental"
    listing = {
        "publication_id": "MLM123456789",
        "catalog_product_id": None,
        "product": "Pasta Dental",
        "price_current": 125.0,
        "price_regular": None,
        "promotion": None,
        "seller": None,
        "sponsored": False,
        "currency": "MXN",
        "url": "https://articulo.mercadolibre.com.mx/MLM-123456789-pasta",
    }
    frames = pilot.build_frames(
        query, [listing],
        {"status": "PARTIAL", "reason": "manual_limit",
         "reported_total": 10, "pages_checked": 1},
        "2026-10-27",
    )
    frame, summary, audit = frames
    assert list(frame.columns) == pilot.COLUMNS
    assert frame["sku"].isna().all()
    assert frame["postal_code"].isna().all()
    assert frame["store_context_verified"].eq(False).all()
    assert audit["publication_id"].iloc[0] == "MLM123456789"
    assert audit["sku_verified"].eq(False).all()
    assert summary["status"].iloc[0] == "PARTIAL"


def test_save_capture_uses_isolated_excel_and_report(tmp_path):
    listing = {
        "publication_id": "MLM123456789",
        "catalog_product_id": None,
        "product": "Colgate",
        "price_current": 149.5,
        "price_regular": None,
        "promotion": None,
        "seller": None,
        "sponsored": True,
        "currency": "MXN",
        "url": "https://articulo.mercadolibre.com.mx/MLM-123456789-colgate",
    }
    report = {
        "query": "colgate",
        "status": "PARTIAL", "reason": "user_limit_reached",
        "collected": 1, "reported_total": 900,
        "pages_checked": 1, "pages": [],
        "source": "mercadolibre_mx_public_search_html",
        "location_verified": False, "sku_verified": False,
    }
    out, diagnostic = pilot.save_capture(
        "colgate", [listing], report,
        pilot_root=tmp_path / "pilots",
        report_root=tmp_path / "diagnostics",
        date="2026-10-27",
    )
    assert out is not None and out.exists()
    assert diagnostic.exists()
    metadata = json.loads(diagnostic.read_text(encoding="utf-8"))
    assert metadata["master_modified"] is False
    assert metadata["publication_id_is_sku"] is False
    xls = pd.ExcelFile(out)
    assert set(xls.sheet_names) == {
        "Concentrado", "Resumen", "Publicaciones"
    }
    product = pd.read_excel(out, sheet_name="Concentrado")
    assert pd.isna(product["sku"].iloc[0])
    ids = pd.read_excel(out, sheet_name="Publicaciones")
    assert ids["publication_id"].iloc[0] == "MLM123456789"


def test_one_environment_only_no_gui_dependencies():
    requirements = (pilot.ROOT / "requirements.txt").read_text(
        encoding="utf-8"
    )
    assert "requests" in requirements
    assert "beautifulsoup4" in requirements
    assert "dash" not in requirements.lower()
    assert "flask" not in requirements.lower()
    assert "app.py" not in str(pilot.ROOT / "scripts" / "test_mercadolibre.py")
