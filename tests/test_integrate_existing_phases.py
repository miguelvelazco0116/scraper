"""Existing captures integration: safety, no browser, and rollback contracts."""
from __future__ import annotations

import json
from datetime import timedelta, datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from main import COLUMNS
from scripts import integrate_existing_phases as target
from scripts.integrate_validated_phase import _write_master
from scripts.integrate_phase2_postal import write_pointer
from scripts.audit_master_phases import inventory

NAMES = {1: [("Farmacias del Ahorro", 6), ("Ibarra Mayoreo", 4),
             ("Chedraui", 20)],
         2: [("Farmacias Guadalajara", 4), ("Soriana", 6)],
         3: [("Bodega Aurrera", 3), ("Walmart", 3)],
         4: [("Farmacias San Pablo", 4), ("Farmacias Benavides", 3)],
         5: [("Farmacias Similares", 2), ("HEB", 2),
             ("Sam's Club", 3), ("Mayorista Duero", 6)]}


def sample(phase: int, day: str):
    products = []
    summary = []
    for name, n in NAMES[phase]:
        for i in range(n):
            cat = name.lower().replace(" ", "-") + "-" + str(i)
            products.append({
                "scrape_timestamp": day + "T11:00:00",
                "retailer": name, "category_id": cat,
                "sku": cat, "price_current": 30 + i,
                "url": "https://example.org/" + cat,
            })
            summary.append({
                "source_phase": f"FASE {phase}", "retailer": name,
                "category_id": cat, "status": "COMPLETE", "products": 1,
            })
    return pd.DataFrame(products).reindex(columns=COLUMNS), pd.DataFrame(summary)


def save_master(root: Path, day: str, phases: tuple[int, ...]):
    frame, summary = None, None
    for phase in phases:
        newframe, newsummary = sample(phase, day)
        frame = newframe if frame is None else pd.concat([frame, newframe], ignore_index=True)
        summary = newsummary if summary is None else pd.concat([summary, newsummary], ignore_index=True)
    file = root / "output" / f"concentrado_productivo_total_{day}.xlsx"
    file.parent.mkdir(parents=True, exist_ok=True)
    control = {
        "capture_date": day, "status": "CHECKPOINT_VALIDATED",
        "products": len(frame), "execution_cases": len(summary),
    }
    for phase in range(1, 6):
        control[f"phase{phase}_status"] = "COMPLETE" if phase in phases else "PENDING"
    _write_master(file, frame, summary, pd.DataFrame([control]))
    pointer = root / "output" / "checkpoints_validos" / "current_master.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    write_pointer(pointer, file, day, list(phases), [])
    return file, pointer


def supply_sources(tmp_path: Path, monkeypatch, phase: int):
    source = tmp_path / "output" / f"source_fase{phase}.xlsx"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"offline-source-checkpoint")
    ready = {name: n for name, n in NAMES[phase]}
    monkeypatch.setattr(target, "source_candidates",
                        lambda p, d: (ready, {}, {str(source): target.checksum(source)}))
    return source


def test_phase1_bootstraps_today_master_without_capturing(tmp_path, monkeypatch):
    day = target.today()
    supply_sources(tmp_path, monkeypatch, 1)
    plan = target.prepare(tmp_path, 1, day)
    assert plan["master_sha"] is None
    assert plan["pointer_sha"] is None
    assert plan["ready"]["Chedraui"] == 20

    def simulate_integrator(args, **kwargs):
        assert "integrate_complete_retailers.py" in args[1]
        assert args[-2:] == ["--phase", "1"]
        save_master(tmp_path, day, (1,))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(target.subprocess, "run", simulate_integrator)
    result = target.commit(tmp_path, plan)
    assert result["status"] == "COMMITTED"
    assert result["phase_status"] == "COMPLETE"
    assert result["master_rows"] == 30
    ptr = json.loads(plan["pointer"].read_text(encoding="utf-8"))
    assert ptr["capture_date"] == day
    assert ptr["completed_phases"] == [1]
    assert inventory(plan["master"])[0].loc[lambda x: x["fase"].eq(1),
                                           "estado_calculado"].iloc[0] == "COMPLETE"


def test_phase3_requires_all_prior_phases_complete(tmp_path, monkeypatch):
    day = target.today()
    file, pointer = save_master(tmp_path, day, (1,))
    supply_sources(tmp_path, monkeypatch, 3)
    with pytest.raises(ValueError, match="Fases previas no COMPLETE"):
        target.prepare(tmp_path, 3, day)
    assert file.is_file()
    assert json.loads(pointer.read_text())["completed_phases"] == [1]
    with pytest.raises(ValueError, match="fecha local"):
        target.prepare(tmp_path, 3, "2026-01-01")


def test_phase3_preserves_prior_retailers_on_integration(tmp_path, monkeypatch):
    day = target.today()
    file, pointer = save_master(tmp_path, day, (1, 2))
    before = target.checksum(file)
    supply_sources(tmp_path, monkeypatch, 3)
    plan = target.prepare(tmp_path, 3, day)
    assert target.checksum(file) == before
    original, oldsummary, _ = target._read_existing_master(file)

    def simulate_integrator(args, **kwargs):
        addframe, addsummary = sample(3, day)
        frame = pd.concat([original, addframe], ignore_index=True)
        cases = pd.concat([oldsummary, addsummary], ignore_index=True)
        control = {"capture_date": day}
        for phase in range(1, 6):
            control[f"phase{phase}_status"] = (
                "COMPLETE" if phase in (1, 2, 3) else "PENDING")
        _write_master(file, frame, cases, pd.DataFrame([control]))
        write_pointer(pointer, file, day, [1, 2, 3], [])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(target.subprocess, "run", simulate_integrator)
    result = target.commit(tmp_path, plan)
    assert result["phase_status"] == "COMPLETE"
    assert result["master_rows"] == 46
    updated, _, _ = target._read_existing_master(file)
    target.check_unchanged_partitions(original, updated, set(plan["ready"]))


def test_rollback_restores_phase3_if_incremental_script_fails(tmp_path, monkeypatch):
    day = target.today()
    file, pointer = save_master(tmp_path, day, (1, 2))
    supply_sources(tmp_path, monkeypatch, 3)
    plan = target.prepare(tmp_path, 3, day)
    before = (target.checksum(file), target.checksum(pointer))
    def fail_after_write(args, **kwargs):
        file.write_bytes(b"broken master")
        pointer.write_text("{}")
        return SimpleNamespace(returncode=2)
    monkeypatch.setattr(target.subprocess, "run", fail_after_write)
    with pytest.raises(ValueError, match="código 2"):
        target.commit(tmp_path, plan)
    assert (target.checksum(file), target.checksum(pointer)) == before


def test_phase5_requires_all_four_previous_phases(tmp_path, monkeypatch):
    day = target.today()
    save_master(tmp_path, day, (1, 2, 3))
    supply_sources(tmp_path, monkeypatch, 5)
    with pytest.raises(ValueError, match="Fases previas no COMPLETE: 4"):
        target.prepare(tmp_path, 5, day)


def test_source_candidates_never_auto_approves_duero(monkeypatch):
    seen = []
    def candidates(phase, day, *, approve_duero_online):
        seen.append(approve_duero_online)
        yield "Mayorista Duero", pd.DataFrame(), pd.DataFrame(), Path("missing.xlsx"), ["Sin evidencia"]
    monkeypatch.setattr(target.incremental, "_candidates", candidates)
    ready, pending, hashes = target.source_candidates(5, target.today())
    assert not ready and "Mayorista Duero" in pending
    assert seen == [False]


def test_existing_phase_has_per_retailer_status_and_never_writes(tmp_path, monkeypatch):
    day = target.today()
    file, pointer = save_master(tmp_path, day, (1, 2, 3))
    before = (target.checksum(file), target.checksum(pointer))
    def no_source_scan(*args, **kwargs):
        raise AssertionError("No need to scan captures if all retailers exist")
    monkeypatch.setattr(target, "source_candidates", no_source_scan)
    plan = target.prepare(tmp_path, 3, day)
    assert plan["ready"] == {}
    assert set(plan["already"]) == {"Bodega Aurrera", "Walmart"}
    assert all(item["status"] == "YA_EXISTE_EN_MASTER"
               for item in plan["retailers"].values())
    monkeypatch.setattr(target.subprocess, "run", no_source_scan)
    result = target.commit(tmp_path, plan)
    assert result["status"] == "SIN_CAMBIOS"
    assert result["master_modified"] is False
    assert (target.checksum(file), target.checksum(pointer)) == before


def test_no_capture_or_invalid_per_retailer_returns_noop(tmp_path, monkeypatch):
    day = target.today()
    file, pointer = save_master(tmp_path, day, (1, 2))
    before = (target.checksum(file), target.checksum(pointer))
    monkeypatch.setattr(target, "source_candidates",
                        lambda p, d: ({}, {
                            "Bodega Aurrera": "Sin fuente de Bodega de la fecha",
                            "Walmart": "Cobertura insuficiente en Excel existente"
                        }, {}))
    plan = target.prepare(tmp_path, 3, day)
    assert plan["ready"] == {}
    assert plan["retailers"]["Bodega Aurrera"]["status"] == "SIN_CAPTURA"
    assert plan["retailers"]["Walmart"]["status"] == "CAPTURA_NO_VALIDADA"
    assert target.commit(tmp_path, plan)["status"] == "SIN_CAMBIOS"
    assert (target.checksum(file), target.checksum(pointer)) == before


def test_partial_existing_phase_only_integrates_missing_retailer(tmp_path, monkeypatch):
    day = target.today()
    file, pointer = save_master(tmp_path, day, (1, 2))
    original, summary, _ = target._read_existing_master(file)
    frame3, cases3 = sample(3, day)
    keep = frame3.loc[frame3["retailer"].eq("Bodega Aurrera")].copy()
    keep_cases = cases3.loc[cases3["retailer"].eq("Bodega Aurrera")].copy()
    data = pd.concat([original, keep], ignore_index=True)
    cases = pd.concat([summary, keep_cases], ignore_index=True)
    control = {"capture_date": day}
    for phase in range(1, 6):
        control[f"phase{phase}_status"] = ("COMPLETE" if phase in (1, 2)
                                            else "PARTIAL_VALIDATED" if phase == 3
                                            else "PENDING")
    _write_master(file, data, cases, pd.DataFrame([control]))
    write_pointer(pointer, file, day, [1, 2], [3])
    before_kept = data.loc[data["retailer"].eq("Bodega Aurrera")].reset_index(drop=True)
    supply_sources(tmp_path, monkeypatch, 3)
    plan = target.prepare(tmp_path, 3, day)
    assert list(plan["ready"]) == ["Walmart"]
    assert plan["retailers"]["Bodega Aurrera"]["status"] == "YA_EXISTE_EN_MASTER"
    assert plan["retailers"]["Walmart"]["status"] == "LISTO_PARA_INTEGRAR"

    def only_missing(args, **kwargs):
        assert args[-2:] == ["--only-retailers", "Walmart"]
        frame = pd.concat(
            [data, frame3.loc[frame3["retailer"].eq("Walmart")]],
            ignore_index=True,
        )
        out_cases = pd.concat(
            [cases, cases3.loc[cases3["retailer"].eq("Walmart")]],
            ignore_index=True,
        )
        control["phase3_status"] = "COMPLETE"
        _write_master(file, frame, out_cases, pd.DataFrame([control]))
        write_pointer(pointer, file, day, [1, 2, 3], [])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(target.subprocess, "run", only_missing)
    result = target.commit(tmp_path, plan)
    assert result["status"] == "COMMITTED"
    assert result["sources_already_in_master"] == {"Bodega Aurrera": 3}
    after, _, _ = target._read_existing_master(file)
    pd.testing.assert_frame_equal(
        before_kept,
        after.loc[after["retailer"].eq("Bodega Aurrera")].reset_index(drop=True),
        check_dtype=False,
    )
