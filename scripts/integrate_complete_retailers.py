from __future__ import annotations

"""Integrate only complete, quality-validated retailers for a given phase.

A PARTIAL retailer never blocks a COMPLETE one. A same-day master is
updated atomically; every write creates an immutable recovery checkpoint.
"""

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd

from integrate_validated_phase import (
    ROOT, PHASES, PHASE3_RETAILER_SOURCES, PHASE4_RETAILER_SOURCES,
    PHASE5_RETAILER_SOURCES, CHECKPOINT_DIR, CURRENT_MASTER_POINTER,
    _canonical, _control_capture_date, _frame_capture_dates,
    _master_path_for_date, _read_current_master_pointer, _read_workbook,
    _read_workbook_optional_control, _read_existing_master,
    _validate_phase, _validate_bodega_retailer, _validate_walmart_retailer,
    _validate_san_pablo_phase, _validate_benavides_phase,
    _validate_similares_phase, _validate_heb_phase, _validate_sams_retailer,
    _validate_duero_retailer, _retailer_category_count, _write_master,
    _write_current_master_pointer,
)
from run_phase2_production import (
    _enabled_category_ids, _validate_fg, _validate_soriana,
)
from scraper.quality_rules import meets_rate
from duero_online_integration_evidence import (
    validate_duero_online_evidence,
    ONLINE_SCOPE,
    MANUAL_DELIVERY_CP,
)

EXPECTED = {
    1: {"Farmacias del Ahorro": 6, "Ibarra Mayoreo": 4, "Chedraui": 20},
    2: {"Farmacias Guadalajara": 4, "Soriana": 6},
    3: {"Bodega Aurrera": 2, "Walmart": 3},
    4: {"Farmacias San Pablo": 4, "Farmacias Benavides": 3},
    5: {"Farmacias Similares": 2, "HEB": 2, "Sam's Club": 3, "Mayorista Duero": 6},
}
PHASE2_FILES = {
    "Farmacias Guadalajara": ROOT / "output" / "farmacias_guadalajara_phase2_test.xlsx",
    "Soriana": ROOT / "output" / "soriana_manual_phase2_test.xlsx",
}
PHASE1_PROGRESS = ROOT / "output" / "fase1_progress.xlsx"
REPORT_DIR = ROOT / "output" / "reportes_integracion"


def _read(path: Path):
    if not path.is_file():
        raise ValueError("Archivo no encontrado: " + str(path))
    return _read_workbook_optional_control(path)


def _date_check(frame: pd.DataFrame, control: pd.DataFrame, day: str):
    dates = _frame_capture_dates(frame)
    if dates != {day}:
        raise ValueError("Fechas de productos inválidas: " + str(sorted(dates)))
    if not control.empty:
        source_date = _control_capture_date(control)
        if source_date is not None and source_date != day:
            raise ValueError("Fecha del Control distinta: " + str(source_date))


def _quality(phase: int, retailer: str, frame: pd.DataFrame, summary: pd.DataFrame):
    expected = EXPECTED[phase][retailer]
    if len(summary) != expected:
        raise ValueError("Casos completos " + str(len(summary)) + "/" + str(expected))
    if not summary["status"].astype(str).str.upper().eq("COMPLETE").all():
        raise ValueError("Existen categorías PARTIAL o no COMPLETE")
    if frame.empty or set(frame["retailer"].dropna().astype(str)) != {retailer}:
        raise ValueError("Concentrado vacío o retailer incorrecto")
    if "products" not in summary.columns:
        raise ValueError("Resumen sin products")
    counts = pd.to_numeric(summary["products"], errors="coerce")
    if counts.isna().any() or counts.le(0).any() or int(counts.sum()) != len(frame):
        raise ValueError("Conteo de productos inconsistente")

    if retailer == "Chedraui":
        keys = summary["store_id"].astype(str).str.replace(r"\.0$", "", regex=True) + "|" + summary["category_id"].astype(str)
    else:
        keys = summary["category_id"].astype(str)
    if keys.nunique() != expected:
        raise ValueError("Casos o categorías duplicados")
    for _, item in summary.iterrows():
        mask = frame["category_id"].astype(str).eq(str(item["category_id"]))
        if retailer == "Chedraui":
            store_id = str(item["store_id"]).removesuffix(".0")
            mask = mask & frame["store_id"].astype(str).str.replace(r"\.0$", "", regex=True).eq(store_id)
        if int(mask.sum()) != int(item["products"]):
            raise ValueError("Conteo por categoría/tienda inconsistente: " + str(item["category_id"]))

    if retailer == "Chedraui":
        stores = summary["store_id"].dropna().astype(str).str.replace(r"\.0$", "", regex=True)
        if set(stores) != {"232", "173", "255", "76"}:
            raise ValueError("Chedraui: faltan tiendas validadas")
        if sorted(summary.groupby(stores).size().tolist()) != [5, 5, 5, 5]:
            raise ValueError("Chedraui: faltan categorías por tienda")
        if "store_context_verified" not in summary or not summary["store_context_verified"].eq(True).all():
            raise ValueError("Chedraui: contexto de tienda no verificado")
        if "store_context_contract_version" not in summary or summary["store_context_contract_version"].isna().any():
            raise ValueError("Chedraui: contrato de tienda no verificado")

    if phase == 1 and retailer == "Chedraui":
        required = pd.to_numeric(summary["price_required"], errors="coerce")
        complete = pd.to_numeric(summary["price_required_complete"], errors="coerce")
        if required.isna().any() or complete.isna().any():
            raise ValueError("Chedraui: precios requeridos no verificados")
        if any(not meets_rate(c, r) for c, r in zip(complete, required) if r > 0):
            raise ValueError("Chedraui: precios requeridos incompletos")
    elif retailer != "Farmacias San Pablo":
        prices = pd.to_numeric(frame["price_current"], errors="coerce").notna().sum()
        if not meets_rate(prices, len(frame)):
            raise ValueError("Precios insuficientes")

    if retailer != "Farmacias San Pablo":
        urls = frame["url"].fillna("").astype(str).str.strip().ne("").sum()
        if not meets_rate(urls, len(frame)):
            raise ValueError("URLs insuficientes")

    if retailer not in {"Ibarra Mayoreo", "Farmacias San Pablo"}:
        skus = frame["sku"].fillna("").astype(str).str.strip().ne("").sum()
        if not meets_rate(skus, len(frame)):
            raise ValueError("SKU insuficiente")


def _phase1_candidates(day: str):
    # A progress file can contain complete retailers even when Chedraui fails.
    for path in (PHASE1_PROGRESS, PHASES[1]["source"]):
        if not path.is_file():
            continue
        try:
            frame, summary, control = _read(path)
            _date_check(frame, control, day)
            if path != PHASE1_PROGRESS:
                errors = _validate_phase(1, frame, summary, control)
                if errors:
                    continue
            for retailer in EXPECTED[1]:
                part = summary.loc[summary["retailer"].astype(str).eq(retailer)].copy()
                rows = frame.loc[frame["retailer"].astype(str).eq(retailer)].copy()
                yield retailer, rows, part, path, []
            return
        except (ValueError, KeyError, OSError):
            continue
    for retailer in EXPECTED[1]:
        yield retailer, pd.DataFrame(), pd.DataFrame(), PHASE1_PROGRESS, ["Sin fuente de Fase 1 de la fecha"]


def _full_phase(phase: int, day: str):
    path = PHASES[phase]["source"]
    if not path.is_file():
        return None
    try:
        frame, summary, control = _read_workbook(path)
        _date_check(frame, control, day)
        errors = _validate_phase(phase, frame, summary, control)
        if errors:
            return None
        return frame, summary, path
    except (ValueError, KeyError, OSError):
        return None


def _phase2_candidates(day: str):
    full = _full_phase(2, day)
    if full:
        frame, summary, path = full
        for retailer in EXPECTED[2]:
            yield retailer, frame.loc[frame["retailer"].eq(retailer)].copy(), summary.loc[summary["retailer"].eq(retailer)].copy(), path, []
        return

    for retailer, path in PHASE2_FILES.items():
        try:
            frame, source_summary, control = _read(path)
            _date_check(frame, control, day)
            expected_ids = _enabled_category_ids(retailer)
            if len(expected_ids) != EXPECTED[2][retailer]:
                raise ValueError("Configuración de categorías inesperada")
            if set(frame["retailer"].dropna().astype(str)) != {retailer}:
                raise ValueError("Retailer del archivo incorrecto")
            rows = (
                _validate_fg(frame, source_summary, expected_ids)
                if retailer == "Farmacias Guadalajara"
                else _validate_soriana(frame, source_summary, expected_ids)
            )
            summary = pd.DataFrame(rows)
            expected_source_status = "SUCCESS" if retailer == "Farmacias Guadalajara" else "COMPLETE"
            if not summary["source_status"].astype(str).str.upper().eq(expected_source_status).all():
                raise ValueError("Estado del origen no validado: " + expected_source_status)
            yield retailer, frame, summary, path, []
        except (ValueError, KeyError, OSError) as exc:
            yield retailer, pd.DataFrame(), pd.DataFrame(), path, [str(exc)]


def _phase3_candidates(day: str):
    full = _full_phase(3, day)
    if full:
        frame, summary, path = full
        for retailer in EXPECTED[3]:
            yield retailer, frame.loc[frame["retailer"].eq(retailer)].copy(), summary.loc[summary["retailer"].eq(retailer)].copy(), path, []
        return

    for retailer, cfg in PHASE3_RETAILER_SOURCES.items():
        path = cfg["source"]
        try:
            frame, summary, control = _read(path)
            _date_check(frame, control, day)
            errors = (
                _validate_bodega_retailer(frame, summary)
                if retailer == "Bodega Aurrera"
                else _validate_walmart_retailer(frame, summary, control)
            )
            if errors:
                raise ValueError("; ".join(errors))
            summary = summary.copy()
            summary["source_status"] = summary["status"].astype(str)
            summary["status"] = "COMPLETE"
            yield retailer, frame, summary, path, []
        except (ValueError, KeyError, OSError) as exc:
            yield retailer, pd.DataFrame(), pd.DataFrame(), path, [str(exc)]


def _phase4_5_candidates(
    phase: int, day: str, *,
    approve_duero_online: bool = False,
):
    sources = PHASE4_RETAILER_SOURCES if phase == 4 else PHASE5_RETAILER_SOURCES
    validators = {
        "Farmacias San Pablo": _validate_san_pablo_phase,
        "Farmacias Benavides": _validate_benavides_phase,
        "Farmacias Similares": _validate_similares_phase,
        "HEB": _validate_heb_phase,
        "Sam's Club": _validate_sams_retailer,
        "Mayorista Duero": _validate_duero_retailer,
    }
    for retailer in EXPECTED[phase]:
        cfg = sources.get(retailer)
        if not cfg:
            yield retailer, pd.DataFrame(), pd.DataFrame(), Path(""), ["Sin fuente configurada"]
            continue
        path = cfg["source"]
        try:
            frame, summary, control = _read(path)
            _date_check(frame, control, day)
            if set(frame["retailer"].dropna().astype(str)) != {retailer}:
                raise ValueError("Retailer del archivo incorrecto")
            if "status" not in summary.columns or not summary["status"].astype(str).str.upper().isin(["COMPLETE", "SUCCESS"]).all():
                raise ValueError("Retailer con categorías no completas")
            if retailer == "Mayorista Duero" and approve_duero_online:
                errors = _validate_duero_retailer(
                    frame, summary, allow_online_catalogue=True
                )
                if errors:
                    raise ValueError("; ".join(errors))
                evidence = validate_duero_online_evidence(
                    frame, summary, day, REPORT_DIR
                )
            else:
                errors = validators[retailer](frame, summary)
                if errors:
                    raise ValueError("; ".join(errors))
                evidence = None
            summary = summary.copy()
            summary["source_status"] = summary["status"].astype(str)
            summary["status"] = "COMPLETE"
            if evidence is not None:
                summary["integration_scope"] = ONLINE_SCOPE
                summary["manual_delivery_cp"] = MANUAL_DELIVERY_CP
                summary["manual_delivery_checked"] = True
                summary["manual_price_sample_checked"] = True
                summary["postal_price_verified"] = False
                summary["online_catalogue_approval"] = "OPERATOR_EXPLICIT"
                summary["evidence_pagination_json"] = evidence["evidence_files"]["pagination"]
                summary["evidence_storefront_json"] = evidence["evidence_files"]["storefront"]
                summary["evidence_packaging_json"] = evidence["evidence_files"]["packaging"]
            if retailer == "Farmacias San Pablo":
                summary["products"] = pd.to_numeric(summary["products_extracted"], errors="coerce")
            yield retailer, frame, summary, path, []
        except (ValueError, KeyError, OSError) as exc:
            yield retailer, pd.DataFrame(), pd.DataFrame(), path, [str(exc)]


def _candidates(
    phase: int, day: str, *,
    approve_duero_online: bool = False,
):
    if phase == 1:
        return _phase1_candidates(day)
    if phase == 2:
        return _phase2_candidates(day)
    if phase == 3:
        return _phase3_candidates(day)
    return _phase4_5_candidates(
        phase, day, approve_duero_online=approve_duero_online
    )


def _phase_states(frame: pd.DataFrame, summary: pd.DataFrame):
    completed = []
    states = {}
    for phase, retailers in EXPECTED.items():
        parts = summary.loc[summary["source_phase"].astype(str).eq("FASE " + str(phase))]
        present = set(frame["retailer"].dropna().astype(str)).intersection(retailers)
        complete = (
            present == set(retailers)
            and len(parts) == sum(retailers.values())
            and parts["status"].astype(str).str.upper().eq("COMPLETE").all()
        )
        if complete:
            completed.append(phase)
            states[phase] = "COMPLETE"
        elif present:
            states[phase] = "PARTIAL_VALIDATED"
        else:
            states[phase] = "PENDING"
    return completed, states


def _control(frame: pd.DataFrame, summary: pd.DataFrame, day: str, phase: int, master: Path):
    completed, states = _phase_states(frame, summary)
    accepted = [p for p, status in states.items() if status == "PARTIAL_VALIDATED"]
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    row = {
        "status": "CHECKPOINT_VALIDATED",
        "updated_at": now,
        "last_phase_added": phase,
        "completed_phases": "|".join(map(str, completed)),
        "accepted_partial_phases": "|".join(map(str, accepted)),
        "products": len(frame),
        "categories": _retailer_category_count(summary),
        "execution_cases": len(summary),
        "retailers": int(frame["retailer"].nunique()),
        "capture_date": day,
        "master": str(master),
    }
    for p in EXPECTED:
        row["phase" + str(p) + "_status"] = states[p]
        row["phase" + str(p) + "_products"] = int(frame["retailer"].isin(EXPECTED[p]).sum())
    for p in (3, 4, 5):
        row["phase" + str(p) + "_validated_retailers"] = " | ".join(
            sorted(set(frame["retailer"].dropna().astype(str)).intersection(EXPECTED[p]))
        )
    return pd.DataFrame([row]), completed, accepted, states


def _report(phase: int, day: str, rows: list[dict], master: Path, committed: bool):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    stem = REPORT_DIR / ("retailers_fase" + str(phase) + "_" + stamp)
    payload = {
        "phase": phase,
        "capture_date": day,
        "committed": committed,
        "master": str(master),
        "added": [r["retailer"] for r in rows if r["result"] == "AGREGADO"],
        "updated": [r["retailer"] for r in rows if r["result"] == "ACTUALIZADO"],
        "pending": [r["retailer"] for r in rows if r["result"] == "PENDIENTE"],
        "retailers": rows,
    }
    stem.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.DataFrame(rows).to_csv(stem.with_suffix(".csv"), index=False, encoding="utf-8-sig")
    print("\nREPORTE DE RETAILERS")
    for r in rows:
        print("  " + r["result"].ljust(12) + " " + r["retailer"].ljust(24) + " " + str(r["products"]).rjust(6) + " productos  " + r["reason"])
    print("AGREGADOS: " + (", ".join(payload["added"]) or "ninguno"))
    print("ACTUALIZADOS: " + (", ".join(payload["updated"]) or "ninguno"))
    print("PENDIENTES: " + (", ".join(payload["pending"]) or "ninguno"))
    print("CSV: " + str(stem.with_suffix(".csv")))
    print("JSON: " + str(stem.with_suffix(".json")))


def main():
    parser = argparse.ArgumentParser(description="Integra retailers completos y reporta parciales.")
    parser.add_argument("--phase", type=int, choices=range(1, 6), required=True)
    parser.add_argument("--preview", action="store_true", help="No modificar master ni puntero.")
    parser.add_argument(
        "--approve-duero-online", action="store_true",
        help=(
            "Aprueba expresamente catálogo público online Duero para Fase 5 "
            "con evidencia del día; NO certifica precios/stock para CP 59690."
        ),
    )
    args = parser.parse_args()
    if args.approve_duero_online and args.phase != 5:
        parser.error("--approve-duero-online solo aplica a Fase 5")
    day = datetime.now().astimezone().date().isoformat()
    master = _master_path_for_date(day)
    pointer = _read_current_master_pointer()

    # Never overwrite a master for today if it is not the active pointer.
    if master.exists() and pointer != master:
        print("ERROR: el master de hoy existe pero no es el puntero activo. No se sobrescribe.")
        return 2
    if master.exists():
        existing_frame, existing_summary, existing_control = _read_existing_master(master)
        if existing_control.empty or _control_capture_date(existing_control) != day:
            print("ERROR: Control del master existente inválido o de otra fecha.")
            return 2
        if _frame_capture_dates(existing_frame) != {day}:
            print("ERROR: el master contiene capturas de otras fechas.")
            return 2
    else:
        existing_frame = pd.DataFrame(columns=_canonical(pd.DataFrame()).columns)
        existing_summary = pd.DataFrame(columns=["source_phase", "retailer", "category_id", "status", "products"])

    rows = []
    additions = []
    for retailer, frame, summary, source, errors in _candidates(
        args.phase, day, approve_duero_online=args.approve_duero_online
    ):
        try:
            if errors:
                raise ValueError("; ".join(errors))
            _quality(args.phase, retailer, frame, summary)
            _date_check(frame, pd.DataFrame(), day)
            summary = summary.copy()
            summary["source_phase"] = "FASE " + str(args.phase)
            summary["retailer"] = retailer
            additions.append((retailer, _canonical(frame), summary))
            result = "ACTUALIZADO" if existing_frame["retailer"].astype(str).eq(retailer).any() else "AGREGADO"
            scope = (
                "COMPLETE; catálogo público auditado; CP 59690: solo entrega "
                "y producto manual; precios/stock por CP NO certificados"
                if retailer == "Mayorista Duero" and args.approve_duero_online
                else "COMPLETE; validado"
            )
            rows.append({
                "retailer": retailer, "result": result,
                "products": len(frame), "reason": scope,
                "source": str(source),
            })
        except (ValueError, KeyError, OSError) as exc:
            rows.append({"retailer": retailer, "result": "PENDIENTE", "products": 0, "reason": str(exc), "source": str(source)})

    print("FASE " + str(args.phase) + " | " + day)
    if not additions:
        _report(args.phase, day, rows, master, committed=False)
        print("Sin nuevos retailers completos. Master sin cambios.")
        return 0
    if args.preview:
        _report(args.phase, day, rows, master, committed=False)
        print("PREVIEW: Master sin cambios.")
        return 0

    combined = existing_frame.copy()
    combined_summary = existing_summary.copy()
    for retailer, frame, summary in additions:
        combined = combined.loc[~combined["retailer"].astype(str).eq(retailer)].copy()
        combined_summary = combined_summary.loc[
            ~(
                combined_summary["retailer"].astype(str).eq(retailer)
                & combined_summary["source_phase"].astype(str).eq("FASE " + str(args.phase))
            )
        ].copy()
        combined = pd.concat([combined, frame], ignore_index=True)
        combined_summary = pd.concat([combined_summary, summary], ignore_index=True, sort=False)

    combined = _canonical(combined)
    control, completed, accepted, states = _control(combined, combined_summary, day, args.phase, master)
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    if master.exists():
        before = CHECKPOINT_DIR / ("before_retailers_fase" + str(args.phase) + "_" + stamp + ".xlsx")
        shutil.copy2(master, before)
        print("Respaldo previo: " + str(before))
    if CURRENT_MASTER_POINTER.exists():
        pointer_backup = CHECKPOINT_DIR / ("pointer_before_retailers_" + stamp + ".json")
        shutil.copy2(CURRENT_MASTER_POINTER, pointer_backup)
    _write_master(master, combined, combined_summary, control)
    checkpoint = CHECKPOINT_DIR / ("checkpoint_retailers_fase" + str(args.phase) + "_" + stamp + ".xlsx")
    shutil.copy2(master, checkpoint)
    _write_current_master_pointer(
        master_path=master, capture_date=day,
        completed_phases=completed, accepted_partial_phases=accepted,
    )
    _report(args.phase, day, rows, master, committed=True)
    print("MASTER: " + str(master))
    print("TOTAL FILAS: " + str(len(combined)))
    print("ESTADOS: " + ", ".join("F" + str(p) + "=" + status for p, status in states.items()))
    print("CHECKPOINT: " + str(checkpoint))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
