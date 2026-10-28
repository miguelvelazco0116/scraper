"""Offline tests for Soriana four-CP pilot read-only audit."""
from __future__ import annotations

import pandas as pd
from scripts import audit_soriana_cp_rotation as audit


def _case(cp="83420", category="cuidado-bucal", day="2026-10-26"):
    rows = pd.DataFrame([{
        "category_id": category,
        "postal_code": cp,
        "scrape_timestamp": day + "T12:00:00",
        "sku": "SKU-1", "url": "https://www.soriana.com/sku-1.html",
        "price_current": 89,
        "store_id": None,
        "availability_status": "AVAILABLE",
    }])
    meta = pd.DataFrame([{
        "category_id": category, "postal_code": cp, "capture_date": day,
        "status": "COMPLETE", "postal_context_verified": True,
        "postal_context_evidence": "header_delivery_cp:" + cp,
        "products": 1,
    }])
    return rows, meta


def test_six_vs_seven_categories():
    assert len(audit.expected_categories(False)) == 6
    assert len(audit.expected_categories(True)) == 7
    assert "gastrointestinales" in audit.expected_categories(True)


def test_readonly_audit_accepts_verified_current_cp_and_online_availability():
    rows, meta = _case()
    result = audit.audit_case(rows, meta, "83420", "cuidado-bucal", "2026-10-26")
    assert result["status"] == "COMPLETE"
    assert result["available"] == 1
    assert result["products"] == 1


def test_readonly_audit_rejects_wrong_date_and_cp_and_no_evidence():
    rows, meta = _case()
    assert audit.audit_case(rows, meta, "44220", "cuidado-bucal", "2026-10-26")[
        "status"
    ] == "PENDING"
    assert audit.audit_case(rows, meta, "83420", "cuidado-bucal", "2026-10-27")[
        "status"
    ] == "PENDING"
    meta.loc[0, "postal_context_evidence"] = "header_delivery_cp:44220"
    assert audit.audit_case(rows, meta, "83420", "cuidado-bucal", "2026-10-26")[
        "status"
    ] == "PENDING"


def test_readonly_audit_rejects_mixed_region_unknown_stock_and_wrong_price():
    rows, meta = _case()
    assert audit.audit_case(
        rows.assign(postal_code="54030"), meta, "83420", "cuidado-bucal",
        "2026-10-26"
    )["status"] == "PENDING"
    assert audit.audit_case(
        rows.assign(availability_status="UNKNOWN"), meta, "83420",
        "cuidado-bucal", "2026-10-26"
    )["status"] == "PENDING"
    assert audit.audit_case(
        rows.assign(price_current=0), meta, "83420", "cuidado-bucal",
        "2026-10-26"
    )["status"] == "PENDING"


def test_readonly_audit_reads_all_cps_and_does_not_touch_master(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "OUTPUT_DIR", tmp_path)
    for cp in audit.CODES:
        rows, meta = _case(cp=cp)
        with pd.ExcelWriter(tmp_path / f"soriana_{cp}.xlsx") as writer:
            rows.to_excel(writer, sheet_name="Concentrado", index=False)
            meta.to_excel(writer, sheet_name="Resumen", index=False)
    result = audit.audit("2026-10-26", include_gastro=False)
    assert len(result) == 24
    assert sum(x["status"] == "COMPLETE" for x in result) == 4
    assert (tmp_path / "soriana_83420.xlsx").exists()
