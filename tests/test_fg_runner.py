from __future__ import annotations

import json

import pytest

import scripts.run_farmacias_guadalajara as runner


def _write_diag(path, *, target=358, links=358, rows=358, final_links=358):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "target_products": target,
                "product_links": links,
                "rows": rows,
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
        rows=356,
        final_links=358,
    )

    result = runner._validate_diagnostic(
        "cuidado-bucal",
        require_complete=True,
        min_row_coverage=0.95,
    )

    assert result["target"] == 358
    assert result["links"] == 358
    assert result["rows"] == 356
    assert result["coverage"] > 0.99


def test_validate_diagnostic_rejects_incomplete_links(monkeypatch, tmp_path):
    diagnostics = tmp_path / "diagnostics"
    monkeypatch.setattr(runner, "DIAGNOSTICS", diagnostics)
    _write_diag(
        diagnostics / "farmacias_guadalajara_cuidado-bucal.json",
        target=358,
        links=340,
        rows=340,
        final_links=340,
    )

    with pytest.raises(RuntimeError, match="cobertura incompleta"):
        runner._validate_diagnostic(
            "cuidado-bucal",
            require_complete=True,
            min_row_coverage=0.95,
        )


def test_preflight_restores_existing_consolidated(monkeypatch, tmp_path):
    diagnostics = tmp_path / "diagnostics"
    control = tmp_path / "control"
    output = tmp_path / "output"
    consolidated = output / "concentrado_scraper.xlsx"
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

    result = runner._preflight(timeout=300, browser_channel="msedge-cdp")

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

    runner._preflight(timeout=300, browser_channel="msedge-cdp")

    assert not consolidated.exists()
