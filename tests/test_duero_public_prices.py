"""Duero Phase 5 is a PUBLIC CATALOGUE PRICE source, not a geolocated shop."""
from datetime import datetime

import pandas as pd
import pytest

from scripts import integrate_complete_retailers as m
from scripts import integrate_existing_phases as existing
from scripts.duero_online_integration_evidence import ONLINE_SCOPE

DAY = datetime.now().astimezone().date().isoformat()


def public_duero():
    frame, cases = [], []
    for i, category in enumerate(m.EXPECTED_DUERO_CATEGORIES):
        frame.append({
            "retailer": "Mayorista Duero", "category_id": category,
            "sku": f"SHOPIFY-VARIANT-{i}", "url": f"https://example.com/duero/{i}",
            "price_current": 10.0 + i,
            "scrape_timestamp": DAY + "T11:00:00",
            # Intentionally no store, location, package, or local stock data.
        })
        cases.append({
            "retailer": "Mayorista Duero", "category_id": category,
            "status": "SUCCESS", "products": 1,
            "pagination_complete": True, "pagination_verified": True,
            "independent_match": True, "blocked_detected": False,
        })
    return pd.DataFrame(frame), pd.DataFrame(cases)


def test_public_catalogue_prices_need_no_location_or_manual_approval():
    frame, cases = public_duero()
    m._validate_duero_public_prices(frame, cases)
    m._quality(5, "Mayorista Duero", frame, cases.assign(status="COMPLETE"))


def test_candidate_includes_duero_without_geolocation_or_packaging(tmp_path, monkeypatch):
    frame, cases = public_duero()
    source = tmp_path / "duero_mayoreo_phase5_test.xlsx"
    source.write_bytes(b"synthetic offline source")
    monkeypatch.setattr(m, "PHASE5_RETAILER_SOURCES",
                        {"Mayorista Duero": {"source": source}})
    monkeypatch.setattr(m, "_read", lambda path: (frame.copy(), cases.copy(), pd.DataFrame()))
    candidates = list(m._candidates(5, DAY, approve_duero_online=False))
    duero = next((row for row in candidates if row[0] == "Mayorista Duero"), None)
    assert duero is not None
    retailer, products, summary, path, errors = duero
    assert not errors
    assert len(products) == 6
    assert products["postal_code"].isna().all()
    assert products["store_id"].isna().all()
    assert products["city"].isna().all()
    assert products["state"].isna().all()
    assert products["store_context_verified"].eq(False).all()
    assert summary["status"].eq("COMPLETE").all()
    assert summary["integration_scope"].eq(ONLINE_SCOPE).all()
    assert summary["postal_price_verified"].eq(False).all()
    assert summary["online_catalogue_approval"].eq("AUTOMATIC_PUBLIC_PRICES_ONLY").all()
    ready, pending, hashes = existing.source_candidates(5, DAY)
    assert ready == {"Mayorista Duero": 6}
    assert not pending.get("Mayorista Duero")
    assert str(path) in hashes


@pytest.mark.parametrize("defect", ["price", "missing_category", "duplicate", "pagination"])
def test_public_catalogue_keeps_price_and_completeness_checks(defect):
    frame, cases = public_duero()
    if defect == "price":
        frame.loc[0, "price_current"] = 0
    elif defect == "missing_category":
        cases = cases.iloc[:-1].copy()
    elif defect == "duplicate":
        frame = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
        cases.loc[0, "products"] = 2
    elif defect == "pagination":
        cases.loc[0, "pagination_verified"] = False
    with pytest.raises(ValueError):
        m._validate_duero_public_prices(frame, cases)


def test_runner_no_longer_blocks_public_duero_for_location_or_manual_cp():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    runner = (root / "scripts" / "run_phase5_production.ps1").read_text(encoding="utf-8")
    assert "REQUIERE APROBACION EXPLICITA" not in runner
    assert "AUDITORIAS SECUNDARIAS DUERO" not in runner
    assert "audit_duero_storefront.py" not in runner
    assert "audit_duero_phase5.py" not in runner
    assert "SIN VALIDACION DE UBICACION NI CP" in runner
