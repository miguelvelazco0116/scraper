from __future__ import annotations

import json

import pytest

import scripts.run_farmacias_guadalajara as runner


def _write_diag(
    path,
    *,
    target=358,
    links=358,
    rows=358,
    final_links=358,
    unique_skus=358,
    unique_urls=358,
):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "target_products": target,
                "product_links": links,
                "rows": rows,
                "unique_skus": unique_skus,
                "unique_urls": unique_urls,
                "complete_against_target": (
                    isinstance(target, int)
                    and target > 0
                    and links >= target
                    and rows >= target
                    and unique_skus >= target
                    and unique_urls >= target
                ),
                "expansion": {
                    "initial_links": 20,
                    "final_links": final_links,
                    "captured_responses": 17,
                    "stop_reason": "target_reached",
                },
            }
        ),
        encoding="utf-8",
    )


def test_validate_diagnostic_accepts_complete_catalog(monkeypatch, tmp_path):
    diagnostics = tmp_path / "diagnostics"
    monkeypatch.setattr(runner, "DIAGNOSTICS", diagnostics)
    _write_diag(
        diagnostics / "farmacias_guadalajara_cuidado-bucal.json",
        target=358,
        links=358,
        rows=358,
        final_links=358,
        unique_skus=358,
        unique_urls=358,
    )

    result = runner._validate_diagnostic(
        "cuidado-bucal",
        require_complete=True,
        min_row_coverage=1.0,
    )

    assert result["target"] == 358
    assert result["links"] == 358
    assert result["rows"] == 358
    assert result["unique_skus"] == 358
    assert result["coverage"] == 1.0


def test_validate_diagnostic_rejects_incomplete_links(monkeypatch, tmp_path):
    diagnostics = tmp_path / "diagnostics"
    monkeypatch.setattr(runner, "DIAGNOSTICS", diagnostics)
    _write_diag(
        diagnostics / "farmacias_guadalajara_cuidado-bucal.json",
        target=358,
        links=340,
        rows=340,
        final_links=340,
        unique_skus=340,
        unique_urls=340,
    )

    with pytest.raises(RuntimeError, match="cobertura incompleta"):
        runner._validate_diagnostic(
            "cuidado-bucal",
            require_complete=True,
            min_row_coverage=1.0,
        )


def test_validate_diagnostic_rejects_missing_target(monkeypatch, tmp_path):
    diagnostics = tmp_path / "diagnostics"
    monkeypatch.setattr(runner, "DIAGNOSTICS", diagnostics)
    _write_diag(
        diagnostics / "farmacias_guadalajara_cuidado-bucal.json",
        target=None,
        links=358,
        rows=358,
        final_links=358,
        unique_skus=358,
        unique_urls=358,
    )

    with pytest.raises(RuntimeError, match="no se pudo determinar target_products"):
        runner._validate_diagnostic(
            "cuidado-bucal",
            require_complete=True,
            min_row_coverage=1.0,
        )


def test_validate_diagnostic_rejects_missing_skus(monkeypatch, tmp_path):
    diagnostics = tmp_path / "diagnostics"
    monkeypatch.setattr(runner, "DIAGNOSTICS", diagnostics)
    _write_diag(
        diagnostics / "farmacias_guadalajara_cuidado-bucal.json",
        target=358,
        links=358,
        rows=358,
        final_links=358,
        unique_skus=357,
        unique_urls=358,
    )

    with pytest.raises(RuntimeError, match="SKUs únicos incompletos"):
        runner._validate_diagnostic(
            "cuidado-bucal",
            require_complete=True,
            min_row_coverage=1.0,
        )


def test_preflight_restores_existing_consolidated(monkeypatch, tmp_path):
    diagnostics = tmp_path / "diagnostics"
    control = tmp_path / "control"
    consolidated = tmp_path / "output" / "concentrado_scraper.xlsx"
    consolidated.parent.mkdir(parents=True)
    consolidated.write_bytes(b"ORIGINAL")

    monkeypatch.setattr(runner, "DIAGNOSTICS", diagnostics)
    monkeypatch.setattr(runner, "CONTROL_DIR", control)
    monkeypatch.setattr(runner, "CONSOLIDATED", consolidated)

    def fake_run_submit(*args, **kwargs):
        consolidated.write_bytes(b"PARTIAL-SMOKE")
        path = diagnostics / "farmacias_guadalajara_cuidado-bucal.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "product_links": 40,
                    "rows": 40,
                    "unique_skus": 40,
                    "unique_urls": 40,
                    "expansion": {
                        "initial_links": 20,
                        "final_links": 40,
                        "captured_responses": 1,
                    },
                }
            ),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(runner, "_run_submit", fake_run_submit)

    result = runner._preflight(timeout=300, browser_channel="chrome-cdp")

    assert result["initial_links"] == 20
    assert result["final_links"] == 40
    assert consolidated.read_bytes() == b"ORIGINAL"


def test_preflight_removes_smoke_output_if_no_previous_consolidated(
    monkeypatch,
    tmp_path,
):
    diagnostics = tmp_path / "diagnostics"
    control = tmp_path / "control"
    consolidated = tmp_path / "output" / "concentrado_scraper.xlsx"

    monkeypatch.setattr(runner, "DIAGNOSTICS", diagnostics)
    monkeypatch.setattr(runner, "CONTROL_DIR", control)
    monkeypatch.setattr(runner, "CONSOLIDATED", consolidated)

    def fake_run_submit(*args, **kwargs):
        consolidated.parent.mkdir(parents=True, exist_ok=True)
        consolidated.write_bytes(b"PARTIAL-SMOKE")
        path = diagnostics / "farmacias_guadalajara_cuidado-bucal.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "product_links": 40,
                    "rows": 40,
                    "unique_skus": 40,
                    "unique_urls": 40,
                    "expansion": {
                        "initial_links": 20,
                        "final_links": 40,
                        "captured_responses": 1,
                    },
                }
            ),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(runner, "_run_submit", fake_run_submit)

    runner._preflight(timeout=300, browser_channel="chrome-cdp")

    assert not consolidated.exists()


def test_category_failure_restores_previous_consolidated(monkeypatch, tmp_path):
    diagnostics = tmp_path / "diagnostics"
    control = tmp_path / "control"
    consolidated = tmp_path / "output" / "concentrado_scraper.xlsx"
    consolidated.parent.mkdir(parents=True)
    consolidated.write_bytes(b"BEFORE")

    monkeypatch.setattr(runner, "DIAGNOSTICS", diagnostics)
    monkeypatch.setattr(runner, "CONTROL_DIR", control)
    monkeypatch.setattr(runner, "CONSOLIDATED", consolidated)
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: None)

    def fake_run_submit(*args, **kwargs):
        consolidated.write_bytes(b"PARTIAL")
        _write_diag(
            diagnostics / "farmacias_guadalajara_cuidado-bucal.json",
            target=358,
            links=300,
            rows=300,
            final_links=300,
            unique_skus=300,
            unique_urls=300,
        )
        return 0

    monkeypatch.setattr(runner, "_run_submit", fake_run_submit)

    with pytest.raises(RuntimeError, match="no se logró una descarga completa"):
        runner._run_category_with_validation(
            "cuidado-bucal",
            max_load_more=100,
            timeout=900,
            browser_channel="chrome-cdp",
            min_row_coverage=1.0,
            attempts=2,
            retry_pause=0,
        )

    assert consolidated.read_bytes() == b"BEFORE"


def test_blocked_category_is_not_retried(monkeypatch, tmp_path):
    control = tmp_path / "control"
    consolidated = tmp_path / "output" / "concentrado_scraper.xlsx"

    monkeypatch.setattr(runner, "CONTROL_DIR", control)
    monkeypatch.setattr(runner, "CONSOLIDATED", consolidated)

    calls = {"count": 0}

    def fake_run_submit(*args, **kwargs):
        calls["count"] += 1
        return 2

    monkeypatch.setattr(runner, "_run_submit", fake_run_submit)

    with pytest.raises(RuntimeError, match="verificación/bloqueo"):
        runner._run_category_with_validation(
            "cuidado-bucal",
            max_load_more=100,
            timeout=900,
            browser_channel="chrome-cdp",
            min_row_coverage=1.0,
            attempts=3,
            retry_pause=0,
        )

    assert calls["count"] == 1
