from __future__ import annotations

import argparse
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

CHECKPOINT_DIR = ROOT / "output" / "checkpoints_validos"
CURRENT_MASTER_POINTER = CHECKPOINT_DIR / "current_master.json"
DIAG_PATH = ROOT / "diagnostics" / "phase_checkpoint" / "last_run.json"

PHASES = {
    1: {
        "label": "FASE 1",
        "source": ROOT / "output" / "concentrado_productivo.xlsx",
        "retailers": {
            "Farmacias del Ahorro",
            "Ibarra Mayoreo",
            "Chedraui",
        },
        "categories": 10,
        "require_fg_names": False,
    },
    2: {
        "label": "FASE 2",
        "source": ROOT / "output" / "concentrado_fase2.xlsx",
        "retailers": {
            "Farmacias Guadalajara",
            "Soriana",
        },
        "categories": 10,
        "require_fg_names": True,
    },
    3: {
        "label": "FASE 3",
        "source": ROOT / "output" / "concentrado_fase3.xlsx",
        "retailers": {
            "Bodega Aurrera",
            "Walmart",
        },
        "categories": 5,
        "require_fg_names": False,
    },
}


def _canonical(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    for column in COLUMNS:
        if column not in work.columns:
            work[column] = None
    return work[COLUMNS]


def _read_workbook(path: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    concentrated = pd.read_excel(
        path,
        sheet_name="Concentrado",
        dtype={"sku": str, "store_id": str},
    )
    summary = pd.read_excel(
        path,
        sheet_name="Resumen",
        dtype={"category_id": str},
    )
    control = pd.read_excel(path, sheet_name="Control")
    return _canonical(concentrated), summary, control


def _validate_phase(
    phase: int,
    frame: pd.DataFrame,
    summary: pd.DataFrame,
    control: pd.DataFrame,
) -> list[str]:
    cfg = PHASES[phase]
    notes: list[str] = []

    if frame.empty:
        notes.append("Concentrado vacío")
    if summary.empty:
        notes.append("Resumen vacío")
        return notes
    if control.empty:
        notes.append("Control vacío")
    else:
        status = str(control.iloc[0].get("status") or "").strip().upper()
        if status != "PRODUCTIVE":
            notes.append(f"Control status={status!r}")

    if len(summary) != cfg["categories"]:
        notes.append(
            f"Categorías {len(summary)}/{cfg['categories']}"
        )

    if "status" not in summary.columns:
        notes.append("Resumen sin status")
    elif not summary["status"].astype(str).str.upper().eq("COMPLETE").all():
        notes.append("Existe al menos una categoría no COMPLETE")

    retailers = set(summary["retailer"].dropna().astype(str))
    if retailers != cfg["retailers"]:
        notes.append(
            f"Retailers {sorted(retailers)} != {sorted(cfg['retailers'])}"
        )

    if "products" not in summary.columns:
        notes.append("Resumen sin products")
    else:
        reported = int(
            pd.to_numeric(summary["products"], errors="coerce")
            .fillna(0)
            .sum()
        )
        if reported != len(frame):
            notes.append(
                f"Suma products={reported} != filas={len(frame)}"
            )

    for metric in ("price_complete", "url_complete"):
        if metric not in summary.columns:
            notes.append(f"Resumen sin {metric}")
            continue
        observed = pd.to_numeric(summary[metric], errors="coerce").fillna(-1)
        products = pd.to_numeric(summary["products"], errors="coerce").fillna(-2)
        if not observed.eq(products).all():
            notes.append(f"{metric} incompleto")

    if phase == 2 and cfg["require_fg_names"]:
        fg = summary.loc[
            summary["retailer"].astype(str).eq("Farmacias Guadalajara")
        ].copy()
        if len(fg) != 4:
            notes.append(f"Guadalajara categorías={len(fg)}/4")
        elif {
            "product_name_complete",
            "product_name_invalid",
            "products",
        }.issubset(fg.columns):
            products = pd.to_numeric(fg["products"], errors="coerce").fillna(-1)
            complete = pd.to_numeric(
                fg["product_name_complete"], errors="coerce"
            ).fillna(-2)
            invalid = pd.to_numeric(
                fg["product_name_invalid"], errors="coerce"
            ).fillna(-1)
            if not complete.eq(products).all():
                notes.append("Guadalajara product_name_complete incompleto")
            if not invalid.eq(0).all():
                notes.append("Guadalajara product_name_invalid > 0")
        else:
            notes.append("Faltan gates de nombres de Guadalajara")

    if phase == 3:
        walmart = summary.loc[
            summary["retailer"].astype(str).eq("Walmart")
        ].copy()
        bodega = summary.loc[
            summary["retailer"].astype(str).eq("Bodega Aurrera")
        ].copy()

        if len(walmart) != 3:
            notes.append(f"Walmart categorías={len(walmart)}/3")
        else:
            required = {
                "products",
                "store_context_complete",
                "store_context_verified",
                "price_order_errors",
                "duplicates_sku_url",
                "catalogue_gate_ok",
            }
            if not required.issubset(walmart.columns):
                notes.append("Faltan gates Walmart en Fase 3")
            else:
                products = pd.to_numeric(
                    walmart["products"], errors="coerce"
                ).fillna(-1)
                store_complete = pd.to_numeric(
                    walmart["store_context_complete"],
                    errors="coerce",
                ).fillna(-2)
                if not store_complete.eq(products).all():
                    notes.append(
                        "Walmart store_context_complete incompleto"
                    )
                if not walmart["store_context_verified"].map(bool).all():
                    notes.append(
                        "Walmart store_context_verified=False"
                    )
                price_errors = pd.to_numeric(
                    walmart["price_order_errors"],
                    errors="coerce",
                ).fillna(-1)
                if not price_errors.eq(0).all():
                    notes.append(
                        "Walmart price_order_errors > 0"
                    )
                duplicates = pd.to_numeric(
                    walmart["duplicates_sku_url"],
                    errors="coerce",
                ).fillna(-1)
                if not duplicates.eq(0).all():
                    notes.append(
                        "Walmart duplicates_sku_url > 0"
                    )
                if not walmart["catalogue_gate_ok"].map(bool).all():
                    notes.append(
                        "Walmart coverage/pagination gate incompleto"
                    )

        if len(bodega) != 2:
            notes.append(f"Bodega Aurrera categorías={len(bodega)}/2")
        else:
            if "price_validation_errors" not in bodega.columns:
                notes.append(
                    "Falta price_validation_errors de Bodega Aurrera"
                )
            else:
                price_errors = pd.to_numeric(
                    bodega["price_validation_errors"],
                    errors="coerce",
                ).fillna(-1)
                if not price_errors.eq(0).all():
                    notes.append(
                        "Bodega Aurrera price_validation_errors > 0"
                    )
            if "catalogue_gate_ok" not in bodega.columns:
                notes.append(
                    "Falta catalogue_gate_ok de Bodega Aurrera"
                )
            elif not bodega["catalogue_gate_ok"].map(bool).all():
                notes.append(
                    "Bodega Aurrera coverage/pagination gate incompleto"
                )

    return notes


def _master_path_for_date(capture_date: str) -> Path:
    return (
        ROOT
        / "output"
        / f"concentrado_productivo_total_{capture_date}.xlsx"
    )


def _read_current_master_pointer() -> Path | None:
    if not CURRENT_MASTER_POINTER.exists():
        return None

    try:
        payload = json.loads(
            CURRENT_MASTER_POINTER.read_text(encoding="utf-8")
        )
        raw_path = str(payload.get("master") or "").strip()
        if not raw_path:
            return None
        return Path(raw_path)
    except Exception:
        return None


def _write_current_master_pointer(
    *,
    master_path: Path,
    capture_date: str,
    completed_phases: list[int],
) -> None:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "master": str(master_path),
        "capture_date": capture_date,
        "completed_phases": completed_phases,
        "updated_at": datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
    }
    with atomic_output_path(CURRENT_MASTER_POINTER) as temporary:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _read_existing_master(
    master_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if not master_path.exists():
        return (
            pd.DataFrame(columns=COLUMNS),
            pd.DataFrame(),
            pd.DataFrame(),
        )
    return _read_workbook(master_path)


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
                    pd.to_numeric(
                        subset["price_current"], errors="coerce"
                    ).notna().sum()
                ),
                "url_complete": int(
                    subset["url"].fillna("").astype(str).str.strip().ne("").sum()
                ),
            }
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
    master_path: Path,
    concentrated: pd.DataFrame,
    summary: pd.DataFrame,
    control: pd.DataFrame,
) -> None:
    retailers = _retailer_summary(concentrated)
    with atomic_output_path(master_path) as temp:
        with pd.ExcelWriter(temp, engine="openpyxl") as writer:
            concentrated.to_excel(writer, index=False, sheet_name="Concentrado")
            summary.to_excel(writer, index=False, sheet_name="Resumen")
            retailers.to_excel(writer, index=False, sheet_name="Retailers")
            control.to_excel(writer, index=False, sheet_name="Control")
            _style(writer)


def _write_diagnostic(payload: dict[str, Any]) -> None:
    DIAG_PATH.parent.mkdir(parents=True, exist_ok=True)
    DIAG_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Integra una fase productiva validada al concentrado maestro."
    )
    parser.add_argument("--phase", type=int, choices=[1, 2, 3], required=True)
    args = parser.parse_args()

    phase = args.phase
    cfg = PHASES[phase]
    source = cfg["source"]

    if not source.exists():
        print(f"ERROR: no existe la salida productiva de {cfg['label']}: {source}")
        return 2

    phase_frame, phase_summary, phase_control = _read_workbook(source)
    notes = _validate_phase(
        phase,
        phase_frame,
        phase_summary,
        phase_control,
    )
    if notes:
        _write_diagnostic(
            {
                "status": "REJECTED",
                "phase": phase,
                "source": str(source),
                "notes": notes,
            }
        )
        print(f"{cfg['label']} NO integrada:")
        for note in notes:
            print(f"- {note}")
        return 2

    now = datetime.now().astimezone()
    run_id = now.strftime("%Y%m%d_%H%M%S")
    generated_at = now.isoformat(timespec="seconds")

    if phase == 1:
        capture_date = now.strftime("%Y-%m-%d")
        master_path = _master_path_for_date(capture_date)
        existing_frame = pd.DataFrame(columns=COLUMNS)
        existing_summary = pd.DataFrame()
        completed = []
    else:
        master_path = _read_current_master_pointer()
        if master_path is None:
            print(
                "ERROR: no existe referencia al concentrado activo. "
                "Primero integra una Fase 1 válida."
            )
            return 2
        capture_date = master_path.stem.rsplit("_", 1)[-1]
        existing_frame, existing_summary, existing_control = (
            _read_existing_master(master_path)
        )
        if existing_frame.empty or existing_control.empty:
            print("ERROR: primero integra una Fase 1 válida al concentrado.")
            return 2
        completed_raw = str(
            existing_control.iloc[0].get("completed_phases") or ""
        )
        completed = [
            int(x)
            for x in completed_raw.split("|")
            if x.strip().isdigit()
        ]
        required_previous = list(range(1, phase))
        missing_previous = [
            item
            for item in required_previous
            if item not in completed
        ]
        if missing_previous:
            missing_text = ", ".join(
                f"Fase {item}" for item in missing_previous
            )
            print(
                "ERROR: faltan fases previas validadas en el concentrado: "
                f"{missing_text}."
            )
            return 2

        phase_retailers = cfg["retailers"]
        existing_frame = existing_frame.loc[
            ~existing_frame["retailer"].astype(str).isin(phase_retailers)
        ].copy()
        if not existing_summary.empty and "source_phase" in existing_summary.columns:
            existing_summary = existing_summary.loc[
                existing_summary["source_phase"].astype(str).ne(cfg["label"])
            ].copy()

    phase_summary = phase_summary.copy()
    if "source_phase" in phase_summary.columns:
        phase_summary["source_phase"] = cfg["label"]
    else:
        phase_summary.insert(0, "source_phase", cfg["label"])

    concentrated = pd.concat(
        [existing_frame, phase_frame],
        ignore_index=True,
    )
    concentrated = _canonical(concentrated)

    summary = pd.concat(
        [existing_summary, phase_summary],
        ignore_index=True,
        sort=False,
    )

    completed = sorted(set(completed + [phase]))
    control = pd.DataFrame(
        [
            {
                "status": "CHECKPOINT_VALIDATED",
                "updated_at": generated_at,
                "last_phase_added": phase,
                "completed_phases": "|".join(str(x) for x in completed),
                "products": len(concentrated),
                "categories": len(summary),
                "retailers": int(concentrated["retailer"].nunique()),
                "phase1_products": int(
                    len(
                        concentrated.loc[
                            concentrated["retailer"].astype(str).isin(
                                PHASES[1]["retailers"]
                            )
                        ]
                    )
                ),
                "phase2_products": int(
                    len(
                        concentrated.loc[
                            concentrated["retailer"].astype(str).isin(
                                PHASES[2]["retailers"]
                            )
                        ]
                    )
                ),
                "phase3_products": int(
                    len(
                        concentrated.loc[
                            concentrated["retailer"].astype(str).isin(
                                PHASES[3]["retailers"]
                            )
                        ]
                    )
                ),
                "capture_date": capture_date,
                "master": str(master_path),
            }
        ]
    )

    _write_master(master_path, concentrated, summary, control)
    _write_current_master_pointer(
        master_path=master_path,
        capture_date=capture_date,
        completed_phases=completed,
    )

    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    checkpoint = (
        CHECKPOINT_DIR
        / f"checkpoint_fase{phase}_{run_id}.xlsx"
    )
    shutil.copy2(master_path, checkpoint)

    metadata = {
        "status": "CHECKPOINT_VALIDATED",
        "phase_added": phase,
        "completed_phases": completed,
        "generated_at": generated_at,
        "source": str(source),
        "capture_date": capture_date,
        "master": str(master_path),
        "checkpoint": str(checkpoint),
        "products": len(concentrated),
        "categories": len(summary),
        "retailers": sorted(
            concentrated["retailer"].dropna().astype(str).unique().tolist()
        ),
    }
    checkpoint.with_suffix(".json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_diagnostic(metadata)

    print("=" * 80)
    print(f"{cfg['label']} INTEGRADA AL CONCENTRADO")
    print("=" * 80)
    print(f"Fases completadas : {control.iloc[0]['completed_phases']}")
    print(f"Filas acumuladas  : {len(concentrated)}")
    print(f"Categorías        : {len(summary)}")
    print(f"Retailers         : {control.iloc[0]['retailers']}")
    print(f"Fecha concentrado : {capture_date}")
    print(f"Master            : {master_path}")
    print(f"Checkpoint        : {checkpoint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
