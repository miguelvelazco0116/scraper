from __future__ import annotations

"""Read-only audit for Mayorista Duero's Phase 5 captured workbook.

Does not certify store context or modify the productive master. Flags packed
variants needing unit counts and non-packed variants whose unit count is
unknown, without assuming that every unidentified presentation is a box.
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "output" / "duero_mayoreo_phase5_test.xlsx"
REPORT_DIR = ROOT / "output" / "reportes_integracion"


def audit(path: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    if not path.is_file():
        raise ValueError(f"No existe la captura: {path}")

    frame = pd.read_excel(
        path, sheet_name="Concentrado",
        dtype={"sku": str, "store_id": str, "category_id": str},
    )
    summary = pd.read_excel(
        path, sheet_name="Resumen", dtype={"category_id": str},
    )
    required = {
        "retailer", "category_id", "sku", "product", "url",
        "price_current", "package_type", "units_per_package",
        "price_per_unit", "store_context_verified", "scrape_timestamp",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError("Concentrado sin columnas: " + ", ".join(sorted(missing)))
    if frame.empty or summary.empty:
        raise ValueError("Captura o resumen vacíos")
    if set(frame["retailer"].dropna().astype(str)) != {"Mayorista Duero"}:
        raise ValueError("Retailer inesperado en Concentrado")
    dates = sorted(set(
        frame["scrape_timestamp"].dropna().astype(str).str[:10]
    ))
    if len(dates) != 1:
        raise ValueError("Mezcla de fechas de captura: " + str(dates))

    reported = pd.to_numeric(summary["products"], errors="coerce")
    if reported.isna().any() or int(reported.sum()) != len(frame):
        raise ValueError("Resumen.products no coincide con Concentrado")

    count = pd.to_numeric(frame["units_per_package"], errors="coerce")
    unit_price = pd.to_numeric(frame["price_per_unit"], errors="coerce")
    pack = frame["package_type"].fillna("").astype(str).str.upper().eq("CAJA")
    unknown = count.isna()
    missing_box = pack & unknown
    unknown_non_box = (~pack) & unknown
    has_unit_count = count.gt(0)
    invalid_unit_price = has_unit_count & unit_price.isna()
    store_verified = frame["store_context_verified"].map(
        lambda x: x is True or str(x).strip().lower() in {"true", "1"}
    )
    price_ok = pd.to_numeric(frame["price_current"], errors="coerce").gt(0)
    sku_ok = frame["sku"].fillna("").astype(str).str.strip().ne("")
    url_ok = frame["url"].fillna("").astype(str).str.strip().ne("")
    captured_status = set(summary["status"].dropna().astype(str).str.upper())

    subset = frame.loc[unknown, [
        "category_id", "sku", "product", "package_type",
        "units_per_package", "price_current", "price_per_unit", "url",
    ]].copy()
    subset.insert(
        0, "revision",
        ["CAJA_SIN_UNIDADES" if v else "PRESENTACION_NO_DETERMINADA"
         for v in pack.loc[unknown]],
    )

    groups = frame.groupby("category_id", dropna=False, sort=True)
    breakdown = []
    for category_id, part in groups:
        idx = part.index
        breakdown.append({
            "category_id": str(category_id),
            "variants": len(part),
            "with_unit_count": int(has_unit_count.loc[idx].sum()),
            "unknown_units": int(unknown.loc[idx].sum()),
            "boxes_without_units": int(missing_box.loc[idx].sum()),
            "nonbox_unknown_units": int(unknown_non_box.loc[idx].sum()),
            "store_context_verified": int(store_verified.loc[idx].sum()),
        })
    per_category = pd.DataFrame(breakdown)

    result = {
        "retailer": "Mayorista Duero",
        "capture_date": dates[0],
        "input": str(path),
        "variants": len(frame),
        "categories": len(summary),
        "capture_statuses": sorted(captured_status),
        "unit_count_identified": int(has_unit_count.sum()),
        "unit_count_unknown": int(unknown.sum()),
        "boxes_without_unit_count": int(missing_box.sum()),
        "nonbox_unknown_unit_count": int(unknown_non_box.sum()),
        "identified_units_without_unit_price": int(invalid_unit_price.sum()),
        "sku_complete": int(sku_ok.sum()),
        "positive_price_complete": int(price_ok.sum()),
        "url_complete": int(url_ok.sum()),
        "store_context_verified": int(store_verified.sum()),
        "integration_ready": bool(
            captured_status == {"SUCCESS"}
            and len(summary) == 6
            and int(missing_box.sum()) == 0
            and int(invalid_unit_price.sum()) == 0
            and store_verified.all()
            and price_ok.all()
        ),
        "integration_blockers": [
            name for name, condition in [
                ("CONTEXTO_DE_TIENDA_NO_VERIFICADO", not store_verified.all()),
                ("CAJAS_SIN_UNIDADES", missing_box.any()),
                ("PRECIO_UNITARIO_FALTANTE", invalid_unit_price.any()),
                ("CAPTURA_NO_COMPLETA", captured_status != {"SUCCESS"} or len(summary) != 6),
            ] if condition
        ],
    }
    return per_category, subset, result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Auditar empaques/contexto de Duero sin integrar ni scrapear."
    )
    parser.add_argument("--input", type=Path, default=INPUT)
    args = parser.parse_args()
    try:
        categories, pending, outcome = audit(args.input)
    except (ValueError, KeyError, OSError) as exc:
        print("AUDITORÍA RECHAZADA: " + str(exc))
        return 2

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    base = REPORT_DIR / ("auditoria_duero_fase5_" + stamp)
    pending.to_csv(base.with_suffix(".csv"), encoding="utf-8-sig", index=False)
    outcome["categories_detail"] = categories.to_dict(orient="records")
    base.with_suffix(".json").write_text(
        json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print("=" * 72)
    print("AUDITORÍA DUERO - FASE 5 (SIN MODIFICAR MASTER)")
    print("=" * 72)
    print(categories.to_string(index=False))
    print("")
    print(f"Variantes       : {outcome['variants']}")
    print(f"Unidades válidas: {outcome['unit_count_identified']}")
    print(f"Unidades inciertas: {outcome['unit_count_unknown']}")
    print(f"Cajas sin conteo: {outcome['boxes_without_unit_count']}")
    print(f"Contexto tienda : {outcome['store_context_verified']}/{outcome['variants']}")
    print(f"Integrable      : {outcome['integration_ready']}")
    print("Bloqueos        : " + (", ".join(outcome["integration_blockers"]) or "ninguno"))
    print("Pendientes CSV  : " + str(base.with_suffix(".csv")))
    print("Auditoría JSON  : " + str(base.with_suffix(".json")))
    if not pending.empty:
        print("\nMUESTRA DE PRESENTACIONES POR REVISAR:")
        print(pending[["revision", "category_id", "product"]].head(12).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
