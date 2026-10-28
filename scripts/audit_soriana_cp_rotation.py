"""Read-only audit of Soriana postal-rotation pilot Excel workbooks.

Does not touch master or any capture file. Evaluates 24 existing-category
cases or 28 including the Gastrointestinales pilot, scoped by CP and day.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
from scraper.availability import AVAILABLE, UNAVAILABLE, UNKNOWN
from scraper.config import load_categories

OUTPUT_DIR = ROOT / "output" / "pilots" / "soriana_cp_rotation"
CODES = ("83420", "44220", "54030", "97245")


def expected_categories(include_gastro: bool) -> tuple[str, ...]:
    categories = load_categories(
        ROOT / "config" / "soriana" / "categories.yaml",
        include_disabled=include_gastro,
    )
    result = tuple(c.id for c in categories)
    expected = 7 if include_gastro else 6
    if len(result) != expected or len(set(result)) != expected:
        raise ValueError("La configuración de categorías no es consistente.")
    if include_gastro and "gastrointestinales" not in result:
        raise ValueError("Falta la categoría Gastrointestinales.")
    return result


def audit_case(data: pd.DataFrame, summary: pd.DataFrame, cp: str,
               category: str, day: str) -> dict:
    case = {
        "cp": cp, "category": category, "status": "PENDING",
        "products": 0, "available": 0, "unavailable": 0, "unknown": 0,
        "issue": "",
    }
    meta = summary.loc[summary["category_id"].astype(str).eq(category)]
    subset = data.loc[data["category_id"].astype(str).eq(category)]
    case["products"] = len(subset)
    if len(meta) != 1:
        case["issue"] = "Resumen ausente o duplicado"
        return case
    item = meta.iloc[0]
    if str(item.get("status")) != "COMPLETE":
        case["issue"] = f"Estado {item.get('status')}"
        return case
    if str(item.get("capture_date")) != day:
        case["issue"] = "Resumen fuera de fecha"
        return case
    if str(item.get("postal_code")) != cp:
        case["issue"] = "CP incorrecto en Resumen"
        return case
    if (str(item.get("postal_context_verified")).strip().lower() not in ("true", "1")
        or not str(item.get("postal_context_evidence") or "").startswith(
            f"header_delivery_cp:{cp}"
        )):
        case["issue"] = "CP no certificado en encabezado"
        return case
    if subset.empty or len(subset) != int(item.get("products") or 0):
        case["issue"] = "Filas no coinciden con Resumen"
        return case
    if not subset["postal_code"].astype(str).eq(cp).all():
        case["issue"] = "Filas mezclan ubicaciones"
        return case
    if not subset["scrape_timestamp"].astype(str).str.startswith(day).all():
        case["issue"] = "Filas mezclan fechas"
        return case
    if (subset["store_id"].notna().any()
        or subset["sku"].fillna("").astype(str).str.strip().eq("").any()
        or subset["url"].fillna("").astype(str).str.strip().eq("").any()
        or not pd.to_numeric(
            subset["price_current"], errors="coerce"
        ).gt(0).all()
        or subset.duplicated(["category_id", "sku", "url"]).any()):
        case["issue"] = "Calidad SKU/URL/precio, tienda o duplicados"
        return case

    states = subset["availability_status"].fillna(UNKNOWN).astype(str)
    case["available"] = int(states.eq(AVAILABLE).sum())
    case["unavailable"] = int(states.eq(UNAVAILABLE).sum())
    case["unknown"] = int(states.eq(UNKNOWN).sum())
    known = case["available"] + case["unavailable"]
    if (known + case["unknown"] != len(subset)
        or known / len(subset) < .90):
        case["issue"] = "Disponibilidad online incompleta"
        return case
    case["status"] = "COMPLETE"
    return case


def audit(day: str, *, include_gastro: bool = False, output_dir: Path | None = None) -> list[dict]:
    categories = expected_categories(include_gastro)
    workbooks = output_dir if output_dir is not None else OUTPUT_DIR
    result: list[dict] = []
    for cp in CODES:
        path = workbooks / f"soriana_{cp}.xlsx"
        if not path.is_file():
            result.extend({
                "cp": cp, "category": category, "status": "PENDING",
                "products": 0, "available": 0, "unavailable": 0,
                "unknown": 0, "issue": "Excel no encontrado",
            } for category in categories)
            continue
        try:
            data = pd.read_excel(path, sheet_name="Concentrado",
                                 dtype={"sku": str, "postal_code": str,
                                        "category_id": str, "store_id": str})
            summary = pd.read_excel(path, sheet_name="Resumen",
                                    dtype={"postal_code": str,
                                           "category_id": str})
            required = {
                "category_id", "scrape_timestamp", "postal_code", "store_id",
                "sku", "url", "price_current", "availability_status"
            }
            if not required.issubset(data.columns):
                raise ValueError("Excel carece de columnas de datos requeridas")
            if not {
                "category_id", "status", "capture_date", "postal_code",
                "postal_context_verified", "postal_context_evidence", "products"
            }.issubset(summary.columns):
                raise ValueError("Resumen carece de columnas requeridas")
        except Exception as exc:
            result.extend({
                "cp": cp, "category": category, "status": "PENDING",
                "products": 0, "available": 0, "unavailable": 0,
                "unknown": 0, "issue": f"Excel no legible: {type(exc).__name__}: {exc}",
            } for category in categories)
            continue
        for category in categories:
            result.append(audit_case(data, summary, cp, category, day))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Auditoría solo lectura de Soriana")
    parser.add_argument("--include-gastro", action="store_true")
    parser.add_argument("--day", default=datetime.now().astimezone().date().isoformat())
    args = parser.parse_args()
    rows = audit(args.day, include_gastro=args.include_gastro)
    for row in rows:
        print(
            f"CP {row['cp']} | {row['category']:<32} | "
            f"{row['status']:<9} | {row['products']:>4} productos "
            f"| +{row['available']} / -{row['unavailable']} / ?{row['unknown']}"
            + (f" | {row['issue']}" if row["issue"] else ""),
            flush=True,
        )
    complete = sum(row["status"] == "COMPLETE" for row in rows)
    print(f"\nSORIANA: {complete}/{len(rows)} casos completos "
          f"en {args.day} | MASTER SIN CAMBIOS")
    return 0 if complete == len(rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
