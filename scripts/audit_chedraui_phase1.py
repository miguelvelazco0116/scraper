from __future__ import annotations

"""Read-only status matrix for Chedraui's 20 Phase-1 store/category cases.

Inspect master, Phase-1 progress and isolated store tests without changing
any of them. Never treat isolated COMPLETE as integrated in the master.
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from integrate_complete_retailers import EXPECTED
from integrate_validated_phase import ROOT, _read_current_master_pointer

STORES = (
    ("chedraui-polanco", "232", "Polanco"),
    ("chedraui-tepeyac", "173", "Tepeyac"),
    ("chedraui-cancun-kabah", "255", "Cancún Kabah"),
    ("chedraui-puebla-centro-sur", "76", "Puebla Centro Sur"),
)
CATEGORIES = (
    "higiene-bucal", "lavanderia",
    "desodorantes-y-antitranspirantes",
    "depilacion", "respiratorios",
)
CONTRACT = "chedraui-directory-ui-v3"


def _read(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not path.exists():
        return pd.DataFrame(), pd.DataFrame()
    try:
        frame = pd.read_excel(path, sheet_name="Concentrado", dtype={"sku": str, "store_id": str})
        summary = pd.read_excel(path, sheet_name="Resumen", dtype={"category_id": str, "store_id": str})
        return frame, summary
    except (ValueError, OSError, KeyError, ImportError) as exc:
        print(f"AVISO: no se puede leer {path}: {exc}")
        return pd.DataFrame(), pd.DataFrame()


def _normalized_store(value) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip().removesuffix(".0")


def _yes(value) -> bool:
    return value is True or str(value).strip().lower() in ("true", "1")


def _case(frame: pd.DataFrame, summary: pd.DataFrame, *, store_id: str,
          category_id: str, day: str, source: str,
          require_store_in_summary: bool) -> dict | None:
    if frame.empty or summary.empty:
        return None
    if not {"retailer", "category_id", "status", "products"}.issubset(summary):
        return None
    cases = summary.loc[
        summary["category_id"].fillna("").astype(str).eq(category_id)
    ]
    if "retailer" in cases:
        cases = cases.loc[cases["retailer"].fillna("").astype(str).eq("Chedraui")]
    if "store_id" in cases:
        cases = cases.loc[cases["store_id"].map(_normalized_store).eq(store_id)]
    elif require_store_in_summary:
        return None
    if cases.empty:
        return None

    rows = frame.loc[
        frame["retailer"].fillna("").astype(str).eq("Chedraui")
        & frame["category_id"].fillna("").astype(str).eq(category_id)
    ].copy()
    if "store_id" not in rows:
        return None
    rows = rows.loc[rows["store_id"].map(_normalized_store).eq(store_id)]
    reasons = []
    if len(cases) != 1:
        reasons.append("resumen duplicado")
    item = cases.iloc[-1]
    reported_status = str(item.get("status") or "").strip().upper()
    if reported_status != "COMPLETE":
        reasons.append("status=" + reported_status)
    if not len(rows):
        reasons.append("sin filas")
    expected = pd.to_numeric(pd.Series([item.get("products")]), errors="coerce").iloc[0]
    if pd.isna(expected) or int(expected) != len(rows):
        reasons.append("cantidad diferente al resumen")
    dates = set(rows["scrape_timestamp"].dropna().astype(str).str[:10]) if "scrape_timestamp" in rows else set()
    if dates != {day}:
        reasons.append("fechas != " + day)
    if not _yes(item.get("store_context_verified")):
        reasons.append("tienda no verificada")
    if str(item.get("store_context_contract_version") or "").strip() != CONTRACT:
        reasons.append("contrato de tienda inválido")
    for metric in ("pagination_gaps", "structured_price_missing_pages", "price_order_errors"):
        number = pd.to_numeric(pd.Series([item.get(metric)]), errors="coerce").iloc[0]
        if pd.isna(number) or number != 0:
            reasons.append(metric + " != 0")
    if not rows.empty:
        if not rows["sku"].fillna("").astype(str).str.strip().ne("").all():
            reasons.append("SKU vacío")
        if not rows["url"].fillna("").astype(str).str.strip().ne("").all():
            reasons.append("URL vacía")
        if "is_available" not in rows or not pd.to_numeric(
            rows["is_available"], errors="coerce"
        ).isin([0, 1]).all():
            reasons.append("is_available fuera de 1/0")
        if "store_context_verified" not in rows or not rows["store_context_verified"].map(_yes).all():
            reasons.append("filas sin tienda verificada")
        availability = rows["availability_status"].fillna("UNKNOWN").astype(str).str.upper()
        needed = ~availability.eq("UNAVAILABLE")
        current = pd.to_numeric(rows["price_current"], errors="coerce")
        if not (current.loc[needed].gt(0)).all():
            reasons.append("precio faltante en artículo disponible")
    return {
        "origen": source,
        "status_fuente": reported_status,
        "filas": len(rows),
        "valido": not reasons,
        "problemas": "; ".join(reasons),
    }


def audit(root: Path, day: str, master: Path | None = None):
    master_path = master
    if master_path is None and root == ROOT:
        master_path = _read_current_master_pointer()
    if master_path is None:
        master_path = root / "output" / f"concentrado_productivo_total_{day}.xlsx"

    master_frame, master_summary = _read(master_path)
    progress_frame, progress_summary = _read(root / "output" / "fase1_progress.xlsx")
    isolated = {}
    for store_id, _, _ in STORES:
        stem = store_id.removeprefix("chedraui-").replace("-", "_")
        isolated[store_id] = _read(root / "output" / f"chedraui_{stem}_test.xlsx")

    records = []
    for store_key, store_id, label in STORES:
        for category_id in CATEGORIES:
            collected = []
            for source, frame, summary, require_store in [
                ("MASTER", master_frame, master_summary, True),
                ("PROGRESO_FASE1", progress_frame, progress_summary, True),
                ("TEST_AISLADO", *isolated[store_key], False),
            ]:
                result = _case(
                    frame, summary, store_id=store_id,
                    category_id=category_id, day=day, source=source,
                    require_store_in_summary=require_store,
                )
                if result:
                    collected.append(result)
            integrated = next(
                (item for item in collected if item["origen"] == "MASTER" and item["valido"]),
                None,
            )
            reusable = next(
                (item for item in collected if item["origen"] == "PROGRESO_FASE1" and item["valido"]),
                None,
            )
            tested = next(
                (item for item in collected if item["origen"] == "TEST_AISLADO" and item["valido"]),
                None,
            )
            if integrated:
                state = "INTEGRADO"
            elif reusable:
                state = "VALIDO_EN_PROGRESO"
            elif tested:
                state = "TEST_COMPLETO_SIN_INTEGRAR"
            elif collected:
                state = "REVISAR"
            else:
                state = "SIN_CAPTURA_VALIDA"
            problematic = [
                item["origen"] + ": " + item["problemas"]
                for item in collected if not item["valido"]
            ]
            records.append({
                "tienda": label, "store_id": store_id,
                "categoria": category_id, "estado": state,
                "filas_confirmadas": (
                    integrated or reusable or tested or {"filas": 0}
                )["filas"],
                "observaciones": " | ".join(problematic),
                "fuentes_encontradas": "|".join(i["origen"] for i in collected),
            })
    return pd.DataFrame(records), master_path


def main():
    parser = argparse.ArgumentParser(description="Matriz Chedraui Phase 1: 4 tiendas x 5 categorías (solo lectura).")
    parser.add_argument("--date", default=datetime.now().astimezone().date().isoformat())
    args = parser.parse_args()
    view, master_path = audit(ROOT, args.date)
    report_dir = ROOT / "output" / "reportes_integracion"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    base = report_dir / ("auditoria_chedraui_" + stamp)
    view.to_csv(base.with_suffix(".csv"), index=False, encoding="utf-8-sig")
    payload = {
        "fecha": args.date,
        "master_consultado": str(master_path),
        "total_casos": len(view),
        "casos_integrados": int(view["estado"].eq("INTEGRADO").sum()),
        "casos_en_progreso": int(view["estado"].eq("VALIDO_EN_PROGRESO").sum()),
        "casos_test_aislado": int(view["estado"].eq("TEST_COMPLETO_SIN_INTEGRAR").sum()),
        "pendientes": int(view["estado"].isin(["REVISAR", "SIN_CAPTURA_VALIDA"]).sum()),
        "casos": view.to_dict("records"),
    }
    base.with_suffix(".json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("=" * 100)
    print("CHEDRAUI | AUDITORIA FASE 1 | 4 TIENDAS x 5 CATEGORIAS")
    print("=" * 100)
    print("Master consultado: " + str(master_path))
    print("Fecha de captura: " + args.date)
    print(view[["tienda", "store_id", "categoria", "estado", "filas_confirmadas"]].to_string(index=False))
    print("RESULTADO: " + json.dumps({
        k: v for k, v in payload.items() if k.startswith("casos_") or k == "pendientes"
    }, ensure_ascii=False))
    for _, row in view.loc[view["estado"].eq("REVISAR")].iterrows():
        print("REVISAR " + row["tienda"] + " | " + row["categoria"] + " | " + row["observaciones"])
    print("CSV: " + str(base.with_suffix(".csv")))
    print("JSON: " + str(base.with_suffix(".json")))
    print("Master: SIN CAMBIOS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
