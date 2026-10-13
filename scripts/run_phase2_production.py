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
from scraper.config import load_categories
from scraper.io_utils import atomic_output_path


PHASE = "FASE 2"
RETAILERS = ("Farmacias Guadalajara", "Soriana")

OUTPUT_PATH = ROOT / "output" / "concentrado_fase2.xlsx"
BASELINE_PATH = ROOT / "output" / "fase2_muestra_inicial_valida.xlsx"
BASELINE_META_PATH = ROOT / "output" / "fase2_muestra_inicial_valida.json"

FG_TEST_OUTPUT = ROOT / "output" / "farmacias_guadalajara_phase2_test.xlsx"
SORIANA_TEST_OUTPUT = ROOT / "output" / "soriana_manual_phase2_test.xlsx"

DIAG_DIR = ROOT / "diagnostics" / "phase2_production"
DIAGNOSTIC_PATH = DIAG_DIR / "last_run.json"


def _enabled_category_ids(retailer: str) -> list[str]:
    categories = load_categories(
        ROOT / "config" / retailer / "categories.yaml"
    )
    return [item.id for item in categories]


def _canonical_frame(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    for column in COLUMNS:
        if column not in work.columns:
            work[column] = None
    work = work[COLUMNS]

    if work.empty:
        return work

    sku = work["sku"].fillna("").astype(str).str.strip()
    url = work["url"].fillna("").astype(str).str.strip()
    identified = sku.ne("") | url.ne("")

    with_id = work.loc[identified].drop_duplicates(
        subset=["retailer", "category_id", "sku", "url"],
        keep="last",
    )
    without_id = work.loc[~identified].drop_duplicates(
        subset=["retailer", "category_id", "product", "price_current", "price_raw"],
        keep="last",
    )

    return pd.concat([with_id, without_id], ignore_index=True)


def _count_nonempty(series: pd.Series) -> int:
    return int(
        series.fillna("").astype(str).str.strip().ne("").sum()
    )


def _read_workbook(
    path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, str | None]:
    if not path.exists():
        return (
            pd.DataFrame(columns=COLUMNS),
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
    except Exception as exc:
        return (
            pd.DataFrame(columns=COLUMNS),
            pd.DataFrame(),
            f"no se pudo leer {path}: {type(exc).__name__}: {exc}",
        )

    return _canonical_frame(concentrated), summary, None


def _safe_int(value: Any) -> int | None:
    if value is None or pd.isna(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _source_row(summary: pd.DataFrame, category_id: str) -> pd.Series | None:
    if summary.empty or "category_id" not in summary.columns:
        return None
    matches = summary.loc[
        summary["category_id"].astype(str).eq(category_id)
    ]
    if len(matches) != 1:
        return None
    return matches.iloc[0]


def _invalid_product_name(value: object) -> bool:
    text = str(value or "").strip()
    lowered = text.casefold()
    if not text:
        return True
    return (
        lowered in {"price", "sale price", "precio", "oferta"}
        or lowered.startswith("price reduced from")
        or lowered.startswith("price reduced to")
        or lowered.startswith("reduced from")
        or lowered.startswith("reduced to")
    )


def _validate_fg(
    frame: pd.DataFrame,
    source_summary: pd.DataFrame,
    expected_ids: list[str],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for category_id in expected_ids:
        source = _source_row(source_summary, category_id)
        subset = frame.loc[
            frame["category_id"].astype(str).eq(category_id)
        ].copy()

        notes: list[str] = []
        target = None
        product_links = None
        coverage = None
        expansion_rounds = None
        return_code = None
        source_status = None

        if source is None:
            notes.append("resumen de categoría no disponible")
        else:
            source_status = str(source.get("status") or "").strip().upper()
            target = _safe_int(source.get("target_products"))
            product_links = _safe_int(source.get("product_links"))
            coverage = _safe_float(source.get("coverage"))
            expansion_rounds = _safe_int(source.get("expansion_rounds"))
            return_code = _safe_int(source.get("return_code"))

        products = len(subset)
        product_name_complete = (
            _count_nonempty(subset["product"]) if products else 0
        )
        product_name_invalid = (
            int(subset["product"].apply(_invalid_product_name).sum())
            if products else 0
        )
        sku_complete = _count_nonempty(subset["sku"]) if products else 0
        price_complete = (
            int(pd.to_numeric(subset["price_current"], errors="coerce").notna().sum())
            if products else 0
        )
        url_complete = _count_nonempty(subset["url"]) if products else 0

        if source_status not in {None, "SUCCESS"}:
            notes.append(f"status_origen {source_status}")
        if return_code is None:
            notes.append("return_code no disponible")
        elif return_code != 0:
            notes.append(f"return_code {return_code}")
        if products <= 0:
            notes.append("sin productos")
        if target is None or target <= 0:
            notes.append("target no disponible")
        else:
            actual_coverage = products / target
            coverage = actual_coverage
            if actual_coverage < 0.90:
                notes.append(
                    f"cobertura {actual_coverage:.1%} < 90%"
                )
        if product_name_complete < products:
            notes.append(
                f"producto {product_name_complete}/{products}"
            )
        if product_name_invalid:
            notes.append(
                f"producto_invalido {product_name_invalid}/{products}"
            )
        if sku_complete < products:
            notes.append(f"sku {sku_complete}/{products}")
        if price_complete < products:
            notes.append(f"precio {price_complete}/{products}")
        if url_complete < products:
            notes.append(f"url {url_complete}/{products}")

        if source is not None:
            reported_products = _safe_int(source.get("products"))
            if reported_products is not None and reported_products != products:
                notes.append(
                    f"filas {products} != resumen {reported_products}"
                )

        results.append(
            {
                "phase": PHASE,
                "retailer": "Farmacias Guadalajara",
                "category_id": category_id,
                "status": "COMPLETE" if not notes else "PARTIAL",
                "source_status": source_status,
                "target_products": target,
                "product_links": product_links,
                "products": products,
                "product_name_complete": product_name_complete,
                "product_name_invalid": product_name_invalid,
                "coverage": coverage,
                "coverage_threshold": 0.90,
                "total_pages": None,
                "expansion_rounds": expansion_rounds,
                "sku_complete": sku_complete,
                "price_complete": price_complete,
                "url_complete": url_complete,
                "quality_notes": "; ".join(notes),
            }
        )

    actual_ids = set(
        source_summary.get("category_id", pd.Series(dtype=str))
        .dropna()
        .astype(str)
    )
    extra_ids = sorted(actual_ids - set(expected_ids))
    if extra_ids and results:
        results[0]["status"] = "PARTIAL"
        prefix = results[0]["quality_notes"]
        extra_note = f"categorías inesperadas: {', '.join(extra_ids)}"
        results[0]["quality_notes"] = (
            f"{prefix}; {extra_note}" if prefix else extra_note
        )

    return results


def _validate_soriana(
    frame: pd.DataFrame,
    source_summary: pd.DataFrame,
    expected_ids: list[str],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for category_id in expected_ids:
        source = _source_row(source_summary, category_id)
        subset = frame.loc[
            frame["category_id"].astype(str).eq(category_id)
        ].copy()

        notes: list[str] = []
        target = None
        coverage = None
        total_pages = None
        source_status = None

        if source is None:
            notes.append("resumen de categoría no disponible")
        else:
            source_status = str(source.get("status") or "").strip().upper()
            target = _safe_int(source.get("target_products"))
            coverage = _safe_float(source.get("coverage"))
            total_pages = _safe_int(source.get("total_pages"))

        products = len(subset)
        sku_complete = _count_nonempty(subset["sku"]) if products else 0
        price_complete = (
            int(pd.to_numeric(subset["price_current"], errors="coerce").notna().sum())
            if products else 0
        )
        url_complete = _count_nonempty(subset["url"]) if products else 0

        if source_status not in {None, "COMPLETE"}:
            notes.append(f"status_origen {source_status}")
        if products <= 0:
            notes.append("sin productos")
        if total_pages is None or total_pages <= 0:
            notes.append("paginación no disponible")
        if sku_complete < products:
            notes.append(f"sku {sku_complete}/{products}")
        if price_complete < products:
            notes.append(f"precio {price_complete}/{products}")
        if url_complete < products:
            notes.append(f"url {url_complete}/{products}")

        if source is not None:
            reported_products = _safe_int(source.get("products"))
            if reported_products is not None and reported_products != products:
                notes.append(
                    f"filas {products} != resumen {reported_products}"
                )

        results.append(
            {
                "phase": PHASE,
                "retailer": "Soriana",
                "category_id": category_id,
                "status": "COMPLETE" if not notes else "PARTIAL",
                "source_status": source_status,
                "target_products": target,
                "product_links": None,
                "products": products,
                "coverage": coverage,
                "coverage_threshold": None,
                "total_pages": total_pages,
                "expansion_rounds": None,
                "sku_complete": sku_complete,
                "price_complete": price_complete,
                "url_complete": url_complete,
                "quality_notes": "; ".join(notes),
            }
        )

    actual_ids = set(
        source_summary.get("category_id", pd.Series(dtype=str))
        .dropna()
        .astype(str)
    )
    extra_ids = sorted(actual_ids - set(expected_ids))
    if extra_ids and results:
        results[0]["status"] = "PARTIAL"
        prefix = results[0]["quality_notes"]
        extra_note = f"categorías inesperadas: {', '.join(extra_ids)}"
        results[0]["quality_notes"] = (
            f"{prefix}; {extra_note}" if prefix else extra_note
        )

    return results


def _run_child(label: str, cmd: list[str], output_path: Path) -> int:
    output_path.unlink(missing_ok=True)

    print("")
    print("=" * 80)
    print(label)
    print("=" * 80)

    completed = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
    )
    print(f"{label} exit_code={completed.returncode}")
    return int(completed.returncode)


def _write_productive(
    concentrated: pd.DataFrame,
    summary: pd.DataFrame,
    *,
    generated_at: str,
) -> None:
    control = pd.DataFrame(
        [
            {
                "phase": PHASE,
                "status": "PRODUCTIVE",
                "generated_at": generated_at,
                "capture_date": generated_at[:10],
                "retailers": " | ".join(RETAILERS),
                "categories": len(summary),
                "products": len(concentrated),
                "all_categories_complete": bool(
                    summary["status"].eq("COMPLETE").all()
                ),
                "output": str(OUTPUT_PATH),
            }
        ]
    )

    with atomic_output_path(OUTPUT_PATH) as temporary:
        with pd.ExcelWriter(temporary, engine="openpyxl") as writer:
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
            control.to_excel(
                writer,
                index=False,
                sheet_name="Control",
            )

            workbook = writer.book
            for sheet_name in ("Concentrado", "Resumen", "Control"):
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

            for column in ("P", "Q", "V", "X"):
                for cell in workbook["Concentrado"][column]:
                    if cell.row > 1:
                        cell.number_format = "$#,##0.0000"


def _create_baseline_if_missing(
    *,
    generated_at: str,
    concentrated: pd.DataFrame,
    summary: pd.DataFrame,
) -> bool:
    if BASELINE_PATH.exists():
        return False

    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(OUTPUT_PATH, BASELINE_PATH)

    metadata = {
        "phase": PHASE,
        "status": "VALID_BASELINE",
        "generated_at": generated_at,
        "source": str(OUTPUT_PATH),
        "baseline": str(BASELINE_PATH),
        "products": len(concentrated),
        "categories": len(summary),
        "retailers": list(RETAILERS),
        "note": (
            "Primera muestra productiva valida de Fase 2. "
            "No sobrescribir en corridas futuras."
        ),
    }
    BASELINE_META_PATH.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return True


def _write_diagnostic(payload: dict[str, Any]) -> None:
    DIAG_DIR.mkdir(parents=True, exist_ok=True)
    DIAGNOSTIC_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fase 2 productiva: Farmacias Guadalajara + Soriana "
            "sobre Chrome abierto manualmente."
        )
    )
    parser.add_argument(
        "--cdp-url",
        required=True,
        help="WebSocket CDP del Chrome abierto manualmente.",
    )
    args = parser.parse_args()

    generated_at = datetime.now().astimezone().isoformat(
        timespec="seconds"
    )

    fg_expected = _enabled_category_ids("farmacias-guadalajara")
    soriana_expected = _enabled_category_ids("soriana")
    expected_cases = len(fg_expected) + len(soriana_expected)

    print("=" * 80)
    print("FASE 2 - CORRIDA PRODUCTIVA")
    print("=" * 80)
    print("Retailers : Farmacias Guadalajara + Soriana")
    print(f"Categorías: {expected_cases}")
    print("Navegador : Chrome abierto manualmente")
    print(f"CDP       : {args.cdp_url}")
    print(f"Salida    : {OUTPUT_PATH}")
    print("")
    print(
        "El master sólo se reemplaza si TODAS las categorías "
        "pasan sus gates de calidad."
    )

    fg_code = _run_child(
        "FARMACIAS GUADALAJARA",
        [
            sys.executable,
            str(ROOT / "scripts" / "test_farmacias_guadalajara_phase2.py"),
            "--category",
            "all",
            "--cdp-url",
            args.cdp_url,
        ],
        FG_TEST_OUTPUT,
    )

    soriana_code = _run_child(
        "SORIANA",
        [
            sys.executable,
            str(ROOT / "scripts" / "test_soriana_manual_phase2.py"),
            "--category",
            "all",
            "--cdp-url",
            args.cdp_url,
        ],
        SORIANA_TEST_OUTPUT,
    )

    fg_frame, fg_source_summary, fg_read_error = _read_workbook(
        FG_TEST_OUTPUT
    )
    soriana_frame, soriana_source_summary, soriana_read_error = (
        _read_workbook(SORIANA_TEST_OUTPUT)
    )

    summary_rows = _validate_fg(
        fg_frame,
        fg_source_summary,
        fg_expected,
    )
    summary_rows.extend(
        _validate_soriana(
            soriana_frame,
            soriana_source_summary,
            soriana_expected,
        )
    )
    summary = pd.DataFrame(summary_rows)

    concentrated = pd.concat(
        [fg_frame, soriana_frame],
        ignore_index=True,
    )
    concentrated = _canonical_frame(concentrated)

    expected_products = int(summary["products"].sum()) if not summary.empty else 0
    publication_notes: list[str] = []

    if fg_code != 0:
        publication_notes.append(
            f"Farmacias Guadalajara exit_code={fg_code}"
        )
    if soriana_code != 0:
        publication_notes.append(
            f"Soriana exit_code={soriana_code}"
        )
    if fg_read_error:
        publication_notes.append(
            f"Farmacias Guadalajara: {fg_read_error}"
        )
    if soriana_read_error:
        publication_notes.append(
            f"Soriana: {soriana_read_error}"
        )
    if len(summary) != expected_cases:
        publication_notes.append(
            f"resumen {len(summary)}/{expected_cases} categorías"
        )
    if not summary.empty and not summary["status"].eq("COMPLETE").all():
        publication_notes.append("hay categorías no COMPLETE")
    if len(concentrated) != expected_products:
        publication_notes.append(
            f"filas consolidadas {len(concentrated)} "
            f"!= suma categorías {expected_products}"
        )

    publish_ok = not publication_notes

    diagnostic_payload = {
        "phase": PHASE,
        "generated_at": generated_at,
        "status": "COMPLETE" if publish_ok else "NOT_PUBLISHED",
        "output": str(OUTPUT_PATH),
        "baseline": str(BASELINE_PATH),
        "expected_categories": expected_cases,
        "products": len(concentrated),
        "child_exit_codes": {
            "farmacias_guadalajara": fg_code,
            "soriana": soriana_code,
        },
        "publication_notes": publication_notes,
        "summary": json.loads(
            summary.to_json(
                orient="records",
                force_ascii=False,
            )
        ),
    }
    _write_diagnostic(diagnostic_payload)

    print("")
    print("=" * 80)
    print("RESUMEN FASE 2")
    print("=" * 80)
    if not summary.empty:
        print(
            summary[
                [
                    "retailer",
                    "category_id",
                    "status",
                    "target_products",
                    "products",
                    "product_name_complete",
                    "product_name_invalid",
                    "coverage",
                    "total_pages",
                    "sku_complete",
                    "price_complete",
                    "url_complete",
                    "quality_notes",
                ]
            ].to_string(index=False)
        )

    if not publish_ok:
        print("")
        print("FASE 2 NO PUBLICADA.")
        print(
            "El master productivo anterior NO fue reemplazado."
        )
        for note in publication_notes:
            print(f"- {note}")
        print(f"Diagnóstico: {DIAGNOSTIC_PATH}")
        return 2

    _write_productive(
        concentrated,
        summary,
        generated_at=generated_at,
    )
    baseline_created = _create_baseline_if_missing(
        generated_at=generated_at,
        concentrated=concentrated,
        summary=summary,
    )

    print("")
    print("FASE 2 PUBLICADA CORRECTAMENTE")
    print(f"Filas productivas : {len(concentrated)}")
    print(f"Archivo productivo: {OUTPUT_PATH}")
    print(f"Baseline Fase 2   : {BASELINE_PATH}")
    print(
        "Baseline status   : "
        + (
            "CREADA - MUESTRA INICIAL VALIDA"
            if baseline_created
            else "CONSERVADA - NO SOBRESCRITA"
        )
    )
    print(f"Diagnóstico       : {DIAGNOSTIC_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
