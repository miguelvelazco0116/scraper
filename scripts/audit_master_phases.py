from __future__ import annotations

"""Read-only phase/retailer inventory for the current productive master.

Reports real master rows and case-level statuses. Never changes the master.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integrate_complete_retailers import EXPECTED as PRODUCTIVE_EXPECTED

# Fase 6 is promoted independently, so this read-only audit may report it
# without changing the proven phase-1..5 integrators.
EXPECTED = {**PRODUCTIVE_EXPECTED, 6: {"Farmacias YZA": 2}}
from integrate_validated_phase import (
    _read_current_master_pointer, _master_path_for_date,
    _read_existing_master,
)

REPORT_DIR = ROOT / "output" / "reportes_integracion"


def _safe_int(value):
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def inventory(master: Path):
    frame, summary, control = _read_existing_master(master)
    if frame.empty or summary.empty or control.empty:
        raise ValueError("Faltan las pestañas Concentrado, Resumen o Control")
    required_rows = {"retailer", "category_id", "sku", "price_current", "url"}
    required_cases = {"retailer", "category_id", "source_phase", "status", "products"}
    missing_rows = required_rows - set(frame)
    missing_cases = required_cases - set(summary)
    if missing_rows or missing_cases:
        raise ValueError(
            "Columnas faltantes: Concentrado=" + str(sorted(missing_rows))
            + " Resumen=" + str(sorted(missing_cases))
        )
    rows = []
    detail = []
    row_names = set(frame["retailer"].dropna().astype(str))
    case_names = set(summary["retailer"].dropna().astype(str))
    expected_names = set().union(*(set(v) for v in EXPECTED.values()))
    unknown_retailers = sorted((row_names | case_names) - expected_names)

    for phase, retailers in EXPECTED.items():
        phase_id = f"FASE {phase}"
        for retailer, expected_cases in retailers.items():
            part = frame.loc[
                frame["retailer"].fillna("").astype(str).eq(retailer)
            ]
            cases = summary.loc[
                summary["retailer"].fillna("").astype(str).eq(retailer)
                & summary["source_phase"].fillna("").astype(str).eq(phase_id)
            ]
            total = len(part)
            cases_count = len(cases)
            statuses = sorted(set(cases["status"].dropna().astype(str).str.upper()))
            case_rows = pd.to_numeric(cases["products"], errors="coerce")
            total_reported = int(case_rows.sum()) if case_rows.notna().all() else None
            skus = int(part["sku"].fillna("").astype(str).str.strip().ne("").sum())
            prices = int(
                pd.to_numeric(part["price_current"], errors="coerce")
                .gt(0).sum()
            )
            urls = int(part["url"].fillna("").astype(str).str.strip().ne("").sum())
            availability_count = (
                int(pd.to_numeric(part["is_available"], errors="coerce")
                    .isin([0, 1]).sum())
                if "is_available" in part else 0
            )
            cases_ok = (
                cases_count == expected_cases
                and statuses == ["COMPLETE"]
                and total_reported == total
                and case_rows.gt(0).all()
            )
            if not total:
                state = "PENDIENTE"
            elif cases_ok:
                state = "INTEGRADO_COMPLETE"
            else:
                state = "INTEGRADO_PARCIAL_O_INCONSISTENTE"
            online_scope = (
                sorted(set(cases["integration_scope"].dropna().astype(str)))
                if "integration_scope" in cases else []
            )
            store_flags = (
                int(part["store_context_verified"].eq(True).sum())
                if "store_context_verified" in part else None
            )
            row = {
                "fase": phase, "retailer": retailer, "estado": state,
                "filas_master": total, "casos_master": cases_count,
                "casos_esperados": expected_cases,
                "categorias_unicas": int(part["category_id"].dropna().astype(str).nunique()),
                "filas_declaradas": total_reported,
                "estatus_casos": "|".join(statuses),
                "sku_completos": skus, "precios_positivos": prices,
                "urls_completas": urls, "disponibilidad_1_0": availability_count,
                "contexto_tienda_verificado": store_flags,
                "alcance": "|".join(online_scope),
            }
            rows.append(row)

            for _, case in cases.iterrows():
                detail.append({
                    "fase": phase, "retailer": retailer,
                    "category_id": str(case["category_id"]),
                    "status": str(case["status"]),
                    "products_reported": _safe_int(case["products"]),
                    "source_status": str(case.get("source_status") or ""),
                    "integration_scope": str(case.get("integration_scope") or ""),
                })

    view = pd.DataFrame(rows)
    cases_detail = pd.DataFrame(detail)
    phase_summary = []
    for phase, expected_retailers in EXPECTED.items():
        section = view.loc[view["fase"].eq(phase)]
        all_complete = bool(
            section["estado"].eq("INTEGRADO_COMPLETE").all()
        )
        some_present = bool(section["filas_master"].gt(0).any())
        computed_state = (
            "COMPLETE" if all_complete
            else "PARTIAL_VALIDATED" if some_present
            else "PENDING"
        )
        control_field = f"phase{phase}_status"
        recorded = (
            str(control.iloc[0][control_field])
            if control_field in control else ("PENDING" if phase == 6 and computed_state == "PENDING" else "NO_REGISTRADO")
        )
        phase_summary.append({
            "fase": phase,
            "retailers_integrados": int(section["filas_master"].gt(0).sum()),
            "retailers_esperados": len(expected_retailers),
            "retailers_complete": int(section["estado"].eq("INTEGRADO_COMPLETE").sum()),
            "filas_master": int(section["filas_master"].sum()),
            "casos_complete": int(
                (cases_detail["fase"].eq(phase)
                 & cases_detail["status"].str.upper().eq("COMPLETE")).sum()
            ) if not cases_detail.empty else 0,
            "casos_esperados": sum(expected_retailers.values()),
            "estado_calculado": computed_state,
            "estado_control": recorded,
            "control_coincide": computed_state == recorded,
        })
    phase_frame = pd.DataFrame(phase_summary)
    actual_rows = len(frame)
    counted_rows = int(view["filas_master"].sum())
    actual_retailers = frame["retailer"].dropna().astype(str).nunique()
    warnings = []
    if counted_rows != actual_rows:
        warnings.append(
            f"Filas no reconciliadas: master={actual_rows}, "
            f"retailers esperados={counted_rows}"
        )
    if unknown_retailers:
        warnings.append("Retailers no configurados: " + ", ".join(unknown_retailers))
    for _, row in phase_frame.iterrows():
        if not row["control_coincide"]:
            warnings.append(
                f"Fase {row['fase']}: Control={row['estado_control']} "
                f"pero recalculado={row['estado_calculado']}"
            )
    for _, row in view.iterrows():
        if row["filas_master"] and row["filas_declaradas"] != row["filas_master"]:
            warnings.append(
                f"{row['retailer']}: filas={row['filas_master']} "
                f"pero Resumen={row['filas_declaradas']}"
            )

    facts = {
        "master": str(master),
        "total_rows": actual_rows,
        "total_retailers": actual_retailers,
        "rows_in_expected_retailers": counted_rows,
        "unknown_retailers": unknown_retailers,
        "phases_complete": int(phase_frame["estado_calculado"].eq("COMPLETE").sum()),
        "warnings": warnings,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    return phase_frame, view, cases_detail, facts


def main():
    parser = argparse.ArgumentParser(
        description="Audita todas las fases y retailers del master (solo lectura)."
    )
    parser.add_argument("--master", type=Path)
    parser.add_argument(
        "--categories", action="store_true",
        help="Muestra también el Resumen por categoría/caso",
    )
    args = parser.parse_args()
    master = (
        args.master if args.master else _read_current_master_pointer()
    )
    if master is None:
        today = datetime.now().astimezone().date().isoformat()
        master = _master_path_for_date(today)
    if not master.is_file():
        parser.error("Master productivo no encontrado: " + str(master))
    try:
        phases, retailers, cases, facts = inventory(master)
    except (KeyError, ValueError, OSError) as exc:
        print("ERROR EN AUDITORÍA: " + str(exc))
        return 2

    print("=" * 90)
    print("CONCENTRADO PRODUCTIVO - INVENTARIO DE FASES Y RETAILERS")
    print("=" * 90)
    print("Master: " + facts["master"])
    print(
        f"Filas reales: {facts['total_rows']:,} | "
        f"Retailers: {facts['total_retailers']} | "
        f"Fases completas: {facts['phases_complete']}/{len(EXPECTED)}"
    )
    print("\nFASES")
    print(phases.to_string(index=False))
    print("\nRETAILERS")
    print(retailers[[
        "fase", "retailer", "estado", "filas_master",
        "casos_master", "casos_esperados", "estatus_casos", "alcance",
    ]].to_string(index=False))
    if args.categories and not cases.empty:
        print("\nDETALLE DE CATEGORÍAS")
        print(cases.to_string(index=False))

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    base = REPORT_DIR / ("auditoria_master_fases_" + stamp)
    pd.DataFrame(retailers).to_csv(
        base.with_suffix(".csv"), index=False, encoding="utf-8-sig"
    )
    payload = {
        **facts,
        "phases": phases.to_dict("records"),
        "retailers": retailers.to_dict("records"),
        "categories": cases.to_dict("records"),
    }
    base.with_suffix(".json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print("\nRECONCILIACIÓN")
    if facts["warnings"]:
        for warning in facts["warnings"]:
            print("  AVISO: " + warning)
    else:
        print("  OK: todas las fases coinciden con Control y las filas concilian")
    print("CSV: " + str(base.with_suffix(".csv")))
    print("JSON: " + str(base.with_suffix(".json")))
    print("Master: SIN CAMBIOS")
    return 0 if not facts["warnings"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
