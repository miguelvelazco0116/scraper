from __future__ import annotations

import argparse
import json
import shutil
import subprocess
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
from scraper.io_utils import atomic_output_path, exclusive_file_lock


MASTER_OUTPUT = ROOT / "output" / "concentrado_productivo_total.xlsx"
MASTER_BASELINE = ROOT / "output" / "muestra_inicial_valida_total.xlsx"
MASTER_BASELINE_META = ROOT / "output" / "muestra_inicial_valida_total.json"

PHASE1_OUTPUT = ROOT / "output" / "concentrado_productivo.xlsx"
PHASE2_OUTPUT = ROOT / "output" / "concentrado_fase2.xlsx"

EXTRACT_DIR = ROOT / "output" / "extractos_validos"
MANIFEST_PATH = EXTRACT_DIR / "manifest.csv"

DIAG_DIR = ROOT / "diagnostics" / "production_master"
DIAGNOSTIC_PATH = DIAG_DIR / "last_run.json"

EXPECTED_PHASE1_RETAILERS = {
    "Farmacias del Ahorro",
    "Ibarra Mayoreo",
    "Chedraui",
}
EXPECTED_PHASE2_RETAILERS = {
    "Farmacias Guadalajara",
    "Soriana",
}
EXPECTED_PHASE1_CATEGORIES = 10
EXPECTED_PHASE2_CATEGORIES = 10


def _canonical_frame(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    for column in COLUMNS:
        if column not in work.columns:
            work[column] = None
    return work[COLUMNS]


def _read_phase_workbook(
    path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, str | None]:
    if not path.exists():
        return (
            pd.DataFrame(columns=COLUMNS),
            pd.DataFrame(),
            pd.DataFrame(),
            f"archivo no generado: {path}",
        )

    try:
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
        control = pd.read_excel(
            path,
            sheet_name="Control",
        )
    except Exception as exc:
        return (
            pd.DataFrame(columns=COLUMNS),
            pd.DataFrame(),
            pd.DataFrame(),
            f"no se pudo leer {path}: {type(exc).__name__}: {exc}",
        )

    return _canonical_frame(concentrated), summary, control, None


def _control_value(control: pd.DataFrame, key: str) -> Any:
    if control.empty:
        return None
    return control.iloc[0].get(key)


def _validate_phase(
    *,
    label: str,
    frame: pd.DataFrame,
    summary: pd.DataFrame,
    control: pd.DataFrame,
    expected_retailers: set[str],
    expected_categories: int,
    require_phase2_names: bool = False,
) -> list[str]:
    notes: list[str] = []

    if frame.empty:
        notes.append(f"{label}: concentrado vacío")

    if summary.empty:
        notes.append(f"{label}: resumen vacío")
        return notes

    if control.empty:
        notes.append(f"{label}: control vacío")
    else:
        status = str(_control_value(control, "status") or "").strip().upper()
        if status != "PRODUCTIVE":
            notes.append(f"{label}: control status={status!r}")

        declared_products = pd.to_numeric(
            pd.Series([_control_value(control, "products")]),
            errors="coerce",
        ).iloc[0]
        if pd.notna(declared_products) and int(declared_products) != len(frame):
            notes.append(
                f"{label}: control products={int(declared_products)} "
                f"!= filas={len(frame)}"
            )

    if len(summary) != expected_categories:
        notes.append(
            f"{label}: categorías {len(summary)}/{expected_categories}"
        )

    if "status" not in summary.columns:
        notes.append(f"{label}: resumen sin columna status")
    elif not summary["status"].astype(str).str.upper().eq("COMPLETE").all():
        bad = summary.loc[
            ~summary["status"].astype(str).str.upper().eq("COMPLETE"),
            ["retailer", "category_id", "status"],
        ]
        notes.append(
            f"{label}: categorías no COMPLETE: "
            + bad.to_dict(orient="records").__repr__()
        )

    if "retailer" not in summary.columns:
        notes.append(f"{label}: resumen sin retailer")
    else:
        retailers = set(summary["retailer"].dropna().astype(str))
        if retailers != expected_retailers:
            notes.append(
                f"{label}: retailers {sorted(retailers)} "
                f"!= esperados {sorted(expected_retailers)}"
            )

    if "products" in summary.columns:
        reported = int(
            pd.to_numeric(
                summary["products"],
                errors="coerce",
            ).fillna(0).sum()
        )
        if reported != len(frame):
            notes.append(
                f"{label}: suma productos={reported} != filas={len(frame)}"
            )
    else:
        notes.append(f"{label}: resumen sin products")

    if require_phase2_names:
        fg = summary.loc[
            summary["retailer"].astype(str).eq("Farmacias Guadalajara")
        ].copy()
        if len(fg) != 4:
            notes.append(
                f"{label}: Guadalajara categorías={len(fg)}/4"
            )
        else:
            for column in (
                "product_name_complete",
                "product_name_invalid",
            ):
                if column not in fg.columns:
                    notes.append(
                        f"{label}: falta gate Guadalajara {column}"
                    )

            if {
                "product_name_complete",
                "product_name_invalid",
                "products",
            }.issubset(fg.columns):
                products = pd.to_numeric(
                    fg["products"],
                    errors="coerce",
                ).fillna(0)
                names = pd.to_numeric(
                    fg["product_name_complete"],
                    errors="coerce",
                ).fillna(-1)
                invalid = pd.to_numeric(
                    fg["product_name_invalid"],
                    errors="coerce",
                ).fillna(-1)

                if not names.eq(products).all():
                    notes.append(
                        f"{label}: nombres Guadalajara incompletos"
                    )
                if not invalid.eq(0).all():
                    notes.append(
                        f"{label}: nombres Guadalajara inválidos="
                        f"{int(invalid.clip(lower=0).sum())}"
                    )

        fg_rows = frame.loc[
            frame["retailer"].astype(str).eq(
                "Farmacias Guadalajara"
            )
        ]
        if not fg_rows.empty:
            product_text = (
                fg_rows["product"]
                .fillna("")
                .astype(str)
                .str.strip()
                .str.casefold()
            )
            invalid_mask = (
                product_text.eq("")
                | product_text.eq("price")
                | product_text.eq("sale price")
                | product_text.eq("precio")
                | product_text.eq("oferta")
                | product_text.str.startswith("price reduced from")
                | product_text.str.startswith("price reduced to")
                | product_text.str.startswith("reduced from")
                | product_text.str.startswith("reduced to")
            )
            if bool(invalid_mask.any()):
                notes.append(
                    f"{label}: filas Guadalajara con product inválido="
                    f"{int(invalid_mask.sum())}"
                )

    return notes


def _retailer_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    for retailer, subset in frame.groupby("retailer", dropna=False):
        timestamps = pd.to_datetime(
            subset["scrape_timestamp"],
            errors="coerce",
            utc=True,
        )
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
                    subset["sku"]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                    .ne("")
                    .sum()
                ),
                "price_complete": int(
                    pd.to_numeric(
                        subset["price_current"],
                        errors="coerce",
                    )
                    .notna()
                    .sum()
                ),
                "url_complete": int(
                    subset["url"]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                    .ne("")
                    .sum()
                ),
                "capture_start": (
                    timestamps.min().isoformat()
                    if timestamps.notna().any()
                    else None
                ),
                "capture_end": (
                    timestamps.max().isoformat()
                    if timestamps.notna().any()
                    else None
                ),
            }
        )

    return pd.DataFrame(rows).sort_values(
        "retailer",
        kind="stable",
    ).reset_index(drop=True)


def _style_workbook(writer: pd.ExcelWriter) -> None:
    workbook = writer.book

    for sheet_name in (
        "Concentrado",
        "Resumen",
        "Retailers",
        "Control",
    ):
        ws = workbook[sheet_name]
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

        for cell in ws[1]:
            font = copy(cell.font)
            font.bold = True
            cell.font = font

        for cells in ws.columns:
            sample = [
                str(cell.value) if cell.value is not None else ""
                for cell in cells[:250]
            ]
            width = min(
                max(
                    max(
                        (len(value) for value in sample),
                        default=0,
                    ) + 2,
                    10,
                ),
                42,
            )
            ws.column_dimensions[
                cells[0].column_letter
            ].width = width

    price_headers = {
        "price_current",
        "price_regular",
        "price_per_unit",
        "single_item_price",
    }
    ws = workbook["Concentrado"]
    for cell in ws[1]:
        if cell.value in price_headers:
            for data_cell in ws.iter_cols(
                min_col=cell.column,
                max_col=cell.column,
                min_row=2,
            ):
                for item in data_cell:
                    item.number_format = "$#,##0.0000"


def _write_master(
    *,
    concentrated: pd.DataFrame,
    summary: pd.DataFrame,
    retailer_summary: pd.DataFrame,
    control: pd.DataFrame,
) -> None:
    with atomic_output_path(MASTER_OUTPUT) as temporary:
        with pd.ExcelWriter(
            temporary,
            engine="openpyxl",
        ) as writer:
            concentrated.to_excel(
                writer,
                index=False,
                sheet_name="Concentrado",
            )
            summary.to_excel(
                writer,
                index=False,
                sheet_name="Resumen",
            )
            retailer_summary.to_excel(
                writer,
                index=False,
                sheet_name="Retailers",
            )
            control.to_excel(
                writer,
                index=False,
                sheet_name="Control",
            )
            _style_workbook(writer)


def _atomic_copy(source: Path, target: Path) -> None:
    with atomic_output_path(target) as temporary:
        shutil.copy2(source, temporary)


def _create_baseline_if_missing(
    *,
    generated_at: str,
    run_id: str,
    products: int,
    categories: int,
    retailers: list[str],
) -> bool:
    if MASTER_BASELINE.exists():
        return False

    _atomic_copy(MASTER_OUTPUT, MASTER_BASELINE)

    metadata = {
        "status": "VALID_GLOBAL_BASELINE",
        "generated_at": generated_at,
        "run_id": run_id,
        "source": str(MASTER_OUTPUT),
        "baseline": str(MASTER_BASELINE),
        "products": products,
        "categories": categories,
        "retailers": retailers,
        "note": (
            "Primera corrida productiva integral válida de Fase 1 + Fase 2. "
            "No sobrescribir en corridas futuras."
        ),
    }
    MASTER_BASELINE_META.write_text(
        json.dumps(
            metadata,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return True


def _publish_historical_extract(
    *,
    run_id: str,
    generated_at: str,
    phase1_products: int,
    phase2_products: int,
    products: int,
    categories: int,
    retailers: list[str],
) -> tuple[Path, Path]:
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)

    extract_path = (
        EXTRACT_DIR
        / f"extracto_validado_{run_id}.xlsx"
    )
    metadata_path = (
        EXTRACT_DIR
        / f"extracto_validado_{run_id}.json"
    )

    if extract_path.exists() or metadata_path.exists():
        raise FileExistsError(
            f"Ya existe un extracto para run_id={run_id}"
        )

    _atomic_copy(MASTER_OUTPUT, extract_path)

    metadata = {
        "status": "VALIDATED_EXTRACT",
        "run_id": run_id,
        "generated_at": generated_at,
        "extract": str(extract_path),
        "products": products,
        "phase1_products": phase1_products,
        "phase2_products": phase2_products,
        "categories": categories,
        "retailers": retailers,
    }
    metadata_path.write_text(
        json.dumps(
            metadata,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    manifest_row = pd.DataFrame(
        [
            {
                "run_id": run_id,
                "generated_at": generated_at,
                "extract": str(extract_path),
                "products": products,
                "phase1_products": phase1_products,
                "phase2_products": phase2_products,
                "categories": categories,
                "retailers": len(retailers),
            }
        ]
    )

    with exclusive_file_lock(
        MANIFEST_PATH,
        timeout_seconds=30.0,
    ):
        if MANIFEST_PATH.exists():
            try:
                existing = pd.read_csv(
                    MANIFEST_PATH,
                    dtype={"run_id": str},
                )
            except Exception:
                existing = pd.DataFrame(
                    columns=manifest_row.columns
                )
        else:
            existing = pd.DataFrame(
                columns=manifest_row.columns
            )

        combined = pd.concat(
            [existing, manifest_row],
            ignore_index=True,
        )
        combined = combined.drop_duplicates(
            subset=["run_id"],
            keep="last",
        )

        with atomic_output_path(
            MANIFEST_PATH
        ) as temporary:
            combined.to_csv(
                temporary,
                index=False,
                encoding="utf-8-sig",
            )

    return extract_path, metadata_path


def _write_diagnostic(payload: dict[str, Any]) -> None:
    DIAG_DIR.mkdir(parents=True, exist_ok=True)
    DIAGNOSTIC_PATH.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _run_child(
    label: str,
    cmd: list[str],
) -> int:
    print("")
    print("=" * 84)
    print(label)
    print("=" * 84)

    completed = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
    )
    print(
        f"{label} exit_code={completed.returncode}"
    )
    return int(completed.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Corrida productiva integral: Fase 1 + Fase 2 "
            "+ master + extracto histórico validado."
        )
    )
    parser.add_argument(
        "--cdp-url",
        required=True,
        help="WebSocket CDP del Chrome manual usado por Fase 2.",
    )
    parser.add_argument(
        "--profile-dir",
        default=".chedraui_profile",
        help="Perfil persistente validado de Chedraui.",
    )
    parser.add_argument(
        "--standard-memory",
        action="store_true",
        help="Desactiva LOW-MEMORY para la Fase 1.",
    )
    args = parser.parse_args()

    now = datetime.now().astimezone()
    generated_at = now.isoformat(timespec="seconds")
    run_id = now.strftime("%Y%m%d_%H%M%S")

    print("=" * 84)
    print("SCRAPER PRODUCTIVO INTEGRAL - FASE 1 + FASE 2")
    print("=" * 84)
    print(f"Run ID    : {run_id}")
    print(f"Master    : {MASTER_OUTPUT}")
    print(f"Histórico : {EXTRACT_DIR}")
    print("")
    print(
        "El master y el extracto histórico sólo se publican "
        "si ambas fases terminan válidas."
    )

    phase1_cmd = [
        sys.executable,
        str(
            ROOT
            / "scripts"
            / "run_phase1_production.py"
        ),
        "--profile-dir",
        args.profile_dir,
    ]
    if not args.standard_memory:
        phase1_cmd.append("--low-memory")

    phase1_code = _run_child(
        "FASE 1 PRODUCTIVA",
        phase1_cmd,
    )

    if phase1_code != 0:
        payload = {
            "status": "NOT_PUBLISHED",
            "run_id": run_id,
            "generated_at": generated_at,
            "phase1_exit_code": phase1_code,
            "phase2_exit_code": None,
            "notes": [
                "Fase 1 falló; Fase 2 no se ejecutó."
            ],
        }
        _write_diagnostic(payload)
        print("")
        print("MASTER GENERAL NO PUBLICADO.")
        print(
            "La corrida se detuvo después de Fase 1."
        )
        print(f"Diagnóstico: {DIAGNOSTIC_PATH}")
        return 2

    phase2_code = _run_child(
        "FASE 2 PRODUCTIVA",
        [
            sys.executable,
            str(
                ROOT
                / "scripts"
                / "run_phase2_production.py"
            ),
            "--cdp-url",
            args.cdp_url,
        ],
    )

    phase1_frame, phase1_summary, phase1_control, phase1_error = (
        _read_phase_workbook(PHASE1_OUTPUT)
    )
    phase2_frame, phase2_summary, phase2_control, phase2_error = (
        _read_phase_workbook(PHASE2_OUTPUT)
    )

    notes: list[str] = []

    if phase2_code != 0:
        notes.append(
            f"Fase 2 exit_code={phase2_code}"
        )
    if phase1_error:
        notes.append(f"Fase 1: {phase1_error}")
    if phase2_error:
        notes.append(f"Fase 2: {phase2_error}")

    notes.extend(
        _validate_phase(
            label="Fase 1",
            frame=phase1_frame,
            summary=phase1_summary,
            control=phase1_control,
            expected_retailers=EXPECTED_PHASE1_RETAILERS,
            expected_categories=EXPECTED_PHASE1_CATEGORIES,
        )
    )
    notes.extend(
        _validate_phase(
            label="Fase 2",
            frame=phase2_frame,
            summary=phase2_summary,
            control=phase2_control,
            expected_retailers=EXPECTED_PHASE2_RETAILERS,
            expected_categories=EXPECTED_PHASE2_CATEGORIES,
            require_phase2_names=True,
        )
    )

    overlap = (
        EXPECTED_PHASE1_RETAILERS
        & EXPECTED_PHASE2_RETAILERS
    )
    if overlap:
        notes.append(
            "Retailers duplicados entre fases: "
            + ", ".join(sorted(overlap))
        )

    if notes:
        payload = {
            "status": "NOT_PUBLISHED",
            "run_id": run_id,
            "generated_at": generated_at,
            "phase1_exit_code": phase1_code,
            "phase2_exit_code": phase2_code,
            "notes": notes,
        }
        _write_diagnostic(payload)

        print("")
        print("MASTER GENERAL NO PUBLICADO.")
        for note in notes:
            print(f"- {note}")
        print(
            "El master general y el histórico anterior "
            "se conservan sin cambios."
        )
        print(f"Diagnóstico: {DIAGNOSTIC_PATH}")
        return 2

    phase1_summary = phase1_summary.copy()
    phase2_summary = phase2_summary.copy()
    phase1_summary.insert(0, "source_phase", "FASE 1")
    phase2_summary.insert(0, "source_phase", "FASE 2")

    summary = pd.concat(
        [phase1_summary, phase2_summary],
        ignore_index=True,
        sort=False,
    )

    concentrated = pd.concat(
        [phase1_frame, phase2_frame],
        ignore_index=True,
    )
    concentrated = _canonical_frame(concentrated)

    total_expected = (
        len(phase1_frame)
        + len(phase2_frame)
    )
    if len(concentrated) != total_expected:
        notes.append(
            f"Master filas={len(concentrated)} "
            f"!= Fase1+Fase2={total_expected}"
        )

    all_retailers = sorted(
        set(
            concentrated["retailer"]
            .dropna()
            .astype(str)
        )
    )
    expected_all = sorted(
        EXPECTED_PHASE1_RETAILERS
        | EXPECTED_PHASE2_RETAILERS
    )
    if all_retailers != expected_all:
        notes.append(
            f"Master retailers={all_retailers} "
            f"!= esperados={expected_all}"
        )

    if notes:
        payload = {
            "status": "NOT_PUBLISHED",
            "run_id": run_id,
            "generated_at": generated_at,
            "phase1_exit_code": phase1_code,
            "phase2_exit_code": phase2_code,
            "notes": notes,
        }
        _write_diagnostic(payload)

        print("")
        print("MASTER GENERAL NO PUBLICADO.")
        for note in notes:
            print(f"- {note}")
        print(f"Diagnóstico: {DIAGNOSTIC_PATH}")
        return 2

    retailers = _retailer_summary(concentrated)

    control = pd.DataFrame(
        [
            {
                "status": "PRODUCTIVE",
                "run_id": run_id,
                "generated_at": generated_at,
                "phase1_generated_at": _control_value(
                    phase1_control,
                    "generated_at",
                ),
                "phase2_generated_at": _control_value(
                    phase2_control,
                    "generated_at",
                ),
                "phase1_products": len(phase1_frame),
                "phase2_products": len(phase2_frame),
                "products": len(concentrated),
                "categories": len(summary),
                "retailers": len(all_retailers),
                "all_categories_complete": True,
                "master": str(MASTER_OUTPUT),
            }
        ]
    )

    _write_master(
        concentrated=concentrated,
        summary=summary,
        retailer_summary=retailers,
        control=control,
    )

    baseline_created = _create_baseline_if_missing(
        generated_at=generated_at,
        run_id=run_id,
        products=len(concentrated),
        categories=len(summary),
        retailers=all_retailers,
    )

    extract_path, extract_meta_path = (
        _publish_historical_extract(
            run_id=run_id,
            generated_at=generated_at,
            phase1_products=len(phase1_frame),
            phase2_products=len(phase2_frame),
            products=len(concentrated),
            categories=len(summary),
            retailers=all_retailers,
        )
    )

    payload = {
        "status": "PRODUCTIVE",
        "run_id": run_id,
        "generated_at": generated_at,
        "phase1_exit_code": phase1_code,
        "phase2_exit_code": phase2_code,
        "phase1_products": len(phase1_frame),
        "phase2_products": len(phase2_frame),
        "products": len(concentrated),
        "categories": len(summary),
        "retailers": all_retailers,
        "master": str(MASTER_OUTPUT),
        "baseline": str(MASTER_BASELINE),
        "baseline_created": baseline_created,
        "extract": str(extract_path),
        "extract_metadata": str(
            extract_meta_path
        ),
        "manifest": str(MANIFEST_PATH),
        "notes": [],
    }
    _write_diagnostic(payload)

    print("")
    print("=" * 84)
    print("RESUMEN PRODUCTIVO INTEGRAL")
    print("=" * 84)
    print(
        retailers.to_string(index=False)
    )
    print("")
    print("CORRIDA INTEGRAL PUBLICADA CORRECTAMENTE")
    print(f"Run ID             : {run_id}")
    print(f"Fase 1             : {len(phase1_frame)}")
    print(f"Fase 2             : {len(phase2_frame)}")
    print(f"Filas productivas  : {len(concentrated)}")
    print(f"Categorías         : {len(summary)}")
    print(f"Retailers          : {len(all_retailers)}")
    print(f"Master             : {MASTER_OUTPUT}")
    print(f"Extracto validado  : {extract_path}")
    print(f"Manifest histórico : {MANIFEST_PATH}")
    print(f"Baseline global    : {MASTER_BASELINE}")
    print(
        "Baseline status    : "
        + (
            "CREADA - PRIMERA MUESTRA GLOBAL VALIDA"
            if baseline_created
            else "CONSERVADA - NO SOBRESCRITA"
        )
    )
    print(f"Diagnóstico        : {DIAGNOSTIC_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
