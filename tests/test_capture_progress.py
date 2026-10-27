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
