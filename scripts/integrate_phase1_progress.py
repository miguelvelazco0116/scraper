from __future__ import annotations

import json
import shutil
import sys
from copy import copy
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from main import COLUMNS
from scraper.io_utils import atomic_output_path

PROGRESS_PATH = ROOT / "output" / "fase1_progress.xlsx"
CHECKPOINT_DIR = ROOT / "output" / "checkpoints_validos"
CURRENT_MASTER_POINTER = CHECKPOINT_DIR / "current_master.json"
DIAG_PATH = ROOT / "diagnostics" / "phase_checkpoint" / "phase1_partial_last_run.json"

PHASE1_RETAILERS = {
    "Farmacias del Ahorro",
    "Ibarra Mayoreo",
    "Chedraui",
}


def _canonical(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    for column in COLUMNS:
        if column not in work.columns:
            work[column] = None
    return work[COLUMNS]


def _normalize_store_id(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip().removesuffix(".0")


def _case_key(retailer: str, category_id: str, store_id: Any = None) -> tuple[str, str, str]:
    retailer = str(retailer or "").strip()
    category_id = str(category_id or "").strip()
    store = _normalize_store_id(store_id) if retailer == "Chedraui" else ""
    return retailer, category_id, store


def _capture_date(control: pd.DataFrame, frame: pd.DataFrame) -> str | None:
    if not control.empty:
        value = str(control.iloc[0].get("capture_date") or "").strip()
        if value:
            return value[:10]

    if "scrape_timestamp" in frame.columns:
        dates = set(
            frame["scrape_timestamp"]
            .dropna()
            .astype(str)
            .str.slice(0, 10)
        )
        if len(dates) == 1:
            return next(iter(dates))
    return None


def _master_path(capture_date: str) -> Path:
    return ROOT / "output" / f"concentrado_productivo_total_{capture_date}.xlsx"


def _read_existing_master(path: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if not path.exists():
        return pd.DataFrame(columns=COLUMNS), pd.DataFrame(), pd.DataFrame()

    frame = pd.read_excel(
        path,
        sheet_name="Concentrado",
        dtype={"sku": str, "store_id": str, "category_id": str},
    )
    summary = pd.read_excel(
        path,
        sheet_name="Resumen",
        dtype={"store_id": str, "category_id": str},
    )
    control = pd.read_excel(path, sheet_name="Control")
    return _canonical(frame), summary, control


def _retailer_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for retailer, subset in frame.groupby("retailer", dropna=False):
        rows.append(
            {
                "retailer": retailer,
                "products": len(subset),
                "categories": int(
                    subset["category_id"]
                    .fillna("")
                    .astype(str)
                    .replace("", pd.NA)
                    .nunique(dropna=True)
                ),
                "sku_complete": int(
                    subset["sku"].fillna("").astype(str).str.strip().ne("").sum()
                ),
                "price_complete": int(
                    pd.to_numeric(subset["price_current"], errors="coerce")
                    .notna()
                    .sum()
                ),
                "url_complete": int(
                    subset["url"].fillna("").astype(str).str.strip().ne("").sum()
                ),
            }
        )
    if not rows:
        return pd.DataFrame(
            columns=[
                "retailer",
                "products",
                "categories",
                "sku_complete",
                "price_complete",
                "url_complete",
            ]
        )
    return pd.DataFrame(rows).sort_values("retailer").reset_index(drop=True)


def _style(writer: pd.ExcelWriter) -> None:
    wb = writer.book
    for name in ("Concentrado", "Resumen", "Retailers", "Control"):
        ws = wb[name]
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for cell in ws[1]:
            font = copy(cell.font)
            font.bold = True
            cell.font = font
        for cells in ws.columns:
            values = [
                str(cell.value) if cell.value is not None else ""
                for cell in cells[:250]
            ]
            width = min(
                max(max((len(v) for v in values), default=0) + 2, 10),
                42,
            )
            ws.column_dimensions[cells[0].column_letter].width = width


def _write_master(
    path: Path,
    frame: pd.DataFrame,
    summary: pd.DataFrame,
    control: pd.DataFrame,
) -> None:
    retailers = _retailer_summary(frame)
    with atomic_output_path(path) as temp:
        with pd.ExcelWriter(temp, engine="openpyxl") as writer:
            frame.to_excel(writer, index=False, sheet_name="Concentrado")
            summary.to_excel(writer, index=False, sheet_name="Resumen")
            retailers.to_excel(writer, index=False, sheet_name="Retailers")
            control.to_excel(writer, index=False, sheet_name="Control")
            _style(writer)


def _write_pointer(
    *,
    master_path: Path,
    capture_date: str,
    completed_phases: list[int],
    phase1_status: str,
) -> None:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "master": str(master_path),
        "capture_date": capture_date,
        "completed_phases": completed_phases,
        "phase1_status": phase1_status,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    with atomic_output_path(CURRENT_MASTER_POINTER) as temp:
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _write_diagnostic(payload: dict[str, Any]) -> None:
    DIAG_PATH.parent.mkdir(parents=True, exist_ok=True)
    DIAG_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def main() -> int:
    if not PROGRESS_PATH.exists():
        print(f"ERROR: no existe {PROGRESS_PATH}")
        return 2

    try:
        frame = pd.read_excel(
            PROGRESS_PATH,
            sheet_name="Concentrado",
            dtype={"sku": str, "store_id": str, "category_id": str},
        )
        summary = pd.read_excel(
            PROGRESS_PATH,
            sheet_name="Resumen",
            dtype={"store_id": str, "category_id": str},
        )
        source_control = pd.read_excel(PROGRESS_PATH, sheet_name="Control")
    except Exception as exc:
        print(f"ERROR leyendo progreso Fase 1: {type(exc).__name__}: {exc}")
        return 2

    frame = _canonical(frame)
    capture_date = _capture_date(source_control, frame)
    today = datetime.now().astimezone().date().isoformat()

    if not capture_date:
        print("ERROR: no fue posible determinar la fecha de captura de Fase 1.")
        return 2
    if capture_date != today:
        print(
            "ERROR: fase1_progress.xlsx no pertenece a hoy: "
            f"{capture_date} != {today}."
        )
        return 2

    if summary.empty or "status" not in summary.columns:
        print("ERROR: Resumen de Fase 1 vacío o sin status.")
        return 2

    complete = summary.loc[
        summary["status"].astype(str).str.upper().eq("COMPLETE")
    ].copy()

    if complete.empty:
        print("ERROR: no hay casos COMPLETE para integrar.")
        return 2

    complete_keys = {
        _case_key(
            row.get("retailer"),
            row.get("category_id"),
            row.get("store_id"),
        )
        for _, row in complete.iterrows()
    }

    row_keys = frame.apply(
        lambda row: _case_key(
            row.get("retailer"),
            row.get("category_id"),
            row.get("store_id"),
        ),
        axis=1,
    )
    valid_frame = frame.loc[row_keys.isin(complete_keys)].copy()

    expected_products = int(
        pd.to_numeric(complete["products"], errors="coerce")
        .fillna(0)
        .sum()
    )
    if len(valid_frame) != expected_products:
        print(
            "ERROR: filas COMPLETE no coinciden con Resumen: "
            f"{len(valid_frame)} != {expected_products}."
        )
        return 2

    # Every COMPLETE summary row must have its exact number of rows.
    for _, row in complete.iterrows():
        key = _case_key(
            row.get("retailer"),
            row.get("category_id"),
            row.get("store_id"),
        )
        # Series.eq(tuple) treats the tuple as a list-like operand and
        # raises "Lengths must be equal". Compare tuple objects row by row.
        mask = row_keys.map(lambda value: value == key)
        observed = int(mask.sum())
        expected = int(row.get("products") or 0)
        if observed != expected:
            print(
                "ERROR: caso COMPLETE inconsistente "
                f"{key}: {observed} != {expected}."
            )
            return 2

    complete.insert(0, "source_phase", "FASE 1")

    master_path = _master_path(capture_date)
    existing_frame, existing_summary, existing_control = _read_existing_master(
        master_path
    )

    # Idempotent replacement: refresh only Phase-1 retailers and preserve
    # any unrelated validated partitions already present for the same day.
    if not existing_frame.empty:
        existing_frame = existing_frame.loc[
            ~existing_frame["retailer"].astype(str).isin(PHASE1_RETAILERS)
        ].copy()

    if not existing_summary.empty:
        if "source_phase" in existing_summary.columns:
            existing_summary = existing_summary.loc[
                ~existing_summary["source_phase"].astype(str).eq("FASE 1")
            ].copy()
        elif "retailer" in existing_summary.columns:
            existing_summary = existing_summary.loc[
                ~existing_summary["retailer"].astype(str).isin(PHASE1_RETAILERS)
            ].copy()

    concentrated = pd.concat(
        [existing_frame, valid_frame],
        ignore_index=True,
    )
    concentrated = _canonical(concentrated)

    combined_summary = pd.concat(
        [existing_summary, complete],
        ignore_index=True,
        sort=False,
    )

    completed_phases: list[int] = []
    if not existing_control.empty:
        raw = str(existing_control.iloc[0].get("completed_phases") or "")
        completed_phases = [
            int(value)
            for value in raw.split("|")
            if value.strip().isdigit()
        ]
    completed_phases = sorted(
        value for value in set(completed_phases) if value != 1
    )

    expected_cases = len(summary)
    validated_cases = len(complete)
    pending_cases = expected_cases - validated_cases
    phase1_status = (
        "COMPLETE"
        if pending_cases == 0
        else "PARTIAL_VALIDATED"
    )
    if phase1_status == "COMPLETE":
        completed_phases = sorted(set(completed_phases + [1]))

    now = datetime.now().astimezone()
    generated_at = now.isoformat(timespec="seconds")
    control = pd.DataFrame(
        [
            {
                "status": (
                    "CHECKPOINT_VALIDATED"
                    if phase1_status == "COMPLETE"
                    else "CHECKPOINT_PARTIAL_VALIDATED"
                ),
                "updated_at": generated_at,
                "capture_date": capture_date,
                "last_phase_added": 1,
                "completed_phases": "|".join(
                    str(value) for value in completed_phases
                ),
                "phase1_status": phase1_status,
                "phase1_validated_cases": validated_cases,
                "phase1_expected_cases": expected_cases,
                "phase1_pending_cases": pending_cases,
                "products": len(concentrated),
                "categories": int(
                    combined_summary[["retailer", "category_id"]]
                    .drop_duplicates()
                    .shape[0]
                ) if not combined_summary.empty else 0,
                "execution_cases": len(combined_summary),
            }
        ]
    )

    _write_master(
        master_path,
        concentrated,
        combined_summary,
        control,
    )
    _write_pointer(
        master_path=master_path,
        capture_date=capture_date,
        completed_phases=completed_phases,
        phase1_status=phase1_status,
    )

    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d_%H%M%S")
    checkpoint = (
        CHECKPOINT_DIR
        / f"checkpoint_fase1_partial_{capture_date}_{stamp}.xlsx"
    )
    shutil.copy2(master_path, checkpoint)

    payload = {
        "status": phase1_status,
        "capture_date": capture_date,
        "source": str(PROGRESS_PATH),
        "master": str(master_path),
        "checkpoint": str(checkpoint),
        "validated_cases": validated_cases,
        "expected_cases": expected_cases,
        "pending_cases": pending_cases,
        "validated_products": len(valid_frame),
        "master_products": len(concentrated),
        "completed_phases": completed_phases,
    }
    _write_diagnostic(payload)

    print("=" * 80)
    print("FASE 1 - INTEGRACION DE CASOS COMPLETE")
    print("=" * 80)
    print(f"Fecha             : {capture_date}")
    print(f"Casos validados   : {validated_cases}/{expected_cases}")
    print(f"Casos pendientes  : {pending_cases}")
    print(f"Filas Fase 1      : {len(valid_frame)}")
    print(f"Filas master      : {len(concentrated)}")
    print(f"Estado Fase 1     : {phase1_status}")
    print(
        "Fases completas   : "
        + (
            "|".join(str(value) for value in completed_phases)
            if completed_phases
            else "ninguna"
        )
    )
    print(f"Master            : {master_path}")
    print(f"Checkpoint        : {checkpoint}")
    print("")
    if phase1_status != "COMPLETE":
        print(
            "NOTA: los casos COMPLETE quedaron consolidados, pero Fase 1 "
            "NO se marca como completa."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
