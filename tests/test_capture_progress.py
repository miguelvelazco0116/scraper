"""Offline checks for CLI progress shared with future dashboard display."""
import json

import pytest

from scraper.capture_progress import CaptureProgress, SCHEMA_VERSION


def ledger(tmp_path):
    return CaptureProgress(
        path=tmp_path / "progress.json", retailer="Soriana",
        capture_date="2026-10-27",
        cases=[("83420", "cuidado-bucal"), ("44220", "cuidado-bucal")],
    )


def test_complete_requires_persisted_revalidation(tmp_path):
    progress = ledger(tmp_path)
    progress.start("83420", "cuidado-bucal", attempt=1)
    progress.finish_attempt(
        "83420", "cuidado-bucal", attempt=1,
        status="COMPLETE", products=273,
    )
    before = progress.snapshot()
    assert before["complete_cases"] == 0
    assert before["products_validated"] == 0
    assert before["cases"][0]["estado"] == "CAPTURED"
    progress.reuse("83420", "cuidado-bucal", products=273)
    final = progress.snapshot()
    assert final["schema_version"] == SCHEMA_VERSION
    assert final["complete_cases"] == 1
    assert final["pending_cases"] == 1
    assert final["products_validated"] == 273
    assert final["cases"][0]["accion"] == "OMITIR"
    assert json.loads(progress.path.read_text(encoding="utf-8"))["complete_cases"] == 1


def test_three_attempts_and_no_false_complete(tmp_path):
    progress = ledger(tmp_path)
    for attempt in (1, 2, 3):
        progress.start("83420", "cuidado-bucal", attempt=attempt)
        progress.finish_attempt(
            "83420", "cuidado-bucal", attempt=attempt,
            status="PARTIAL", products=0, error="Tarjetas ausentes"
        )
    first = progress.snapshot()["cases"][0]
    assert first["attempts"] == 3
    assert len(first["retry_history"]) == 3
    assert first["estado"] == "PARTIAL"
    assert first["accion"] == "REINTENTAR"
    assert progress.snapshot()["complete_cases"] == 0
    with pytest.raises(ValueError):
        progress.start("83420", "cuidado-bucal", attempt=4)


def test_blocked_stops_retry(tmp_path):
    progress = ledger(tmp_path)
    progress.start("44220", "cuidado-bucal", attempt=1)
    progress.finish_attempt(
        "44220", "cuidado-bucal", attempt=1,
        status="BLOCKED", products=0, error="Protección del sitio"
    )
    with pytest.raises(ValueError, match="bloqueado"):
        progress.start("44220", "cuidado-bucal", attempt=2)
    entry = progress.snapshot()["cases"][1]
    assert entry["accion"] == "REVISAR"


def test_reuse_checkpoints_skips_attempts(tmp_path):
    progress = ledger(tmp_path)
    progress.reuse("83420", "cuidado-bucal", products=338)
    with pytest.raises(ValueError):
        progress.start("83420", "cuidado-bucal", attempt=1)
    assert progress.snapshot()["attempts_total"] == 0
    assert progress.snapshot()["master_modified"] is False


def test_invalid_sequence_rejected(tmp_path):
    progress = ledger(tmp_path)
    with pytest.raises(ValueError, match="secuencialmente"):
        progress.start("83420", "cuidado-bucal", attempt=2)
    progress.start("83420", "cuidado-bucal", attempt=1)
    with pytest.raises(ValueError):
        progress.start("83420", "cuidado-bucal", attempt=1)


def test_soriana_capture_emits_retry_events(monkeypatch, tmp_path):
    """Capture-case hook is CLI-only and does not require a browser."""
    import pandas as pd
    from scripts import test_soriana_cp_rotation as cli
    from types import SimpleNamespace

    called = []
    statuses = iter(["PARTIAL", "COMPLETE"])

    class Page:
        def wait_for_timeout(self, millis):
            pass

    class FakeScraper:
        def __init__(self, **kwargs):
            self.last_page_meta = {"pages_visited": 1, "pages_expected": 1, "pagination_complete": True}
        def _collect_pages(self, page, category, location):
            return [{"sku": "sku1", "price_current": 70, "url": "https://www.soriana.com/a"}]
        def _save_diagnostics(self, page, name):
            pass

    monkeypatch.setattr(cli, "prepare_postal_category", lambda *args: "header_delivery_cp:83420")
    monkeypatch.setattr(cli, "target_count", lambda page: 1)
    monkeypatch.setattr(cli, "SorianaScraper", FakeScraper)
    monkeypatch.setattr(cli, "normalize_frame", lambda rows: pd.DataFrame(rows))
    monkeypatch.setattr(cli, "classify", lambda *args, **kwargs: {
        "status": next(statuses),
        "products": 1, "available_online": 1, "unavailable_online": 0,
        "availability_unknown": 0, "postal_context_verified": True,
        "quality_notes": "",
    })
    rows, summary = cli.capture_case(
        Page(), SimpleNamespace(id="cuidado-bucal"), None,
        "83420", 10,
        on_attempt=lambda event, attempt, item: called.append((
            event, attempt, item.get("status") if item else None
        ))
    )
    assert len(rows) == 1
    assert summary["status"] == "COMPLETE"
    assert len(json.loads(summary["retry_history"])) == 2
    assert called == [
        ("start", 1, None), ("finish", 1, "PARTIAL"),
        ("start", 2, None), ("finish", 2, "COMPLETE"),
    ]
