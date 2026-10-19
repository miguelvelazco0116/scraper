from __future__ import annotations

"""Immutable, same-day, per-store/category snapshots for Chedraui isolated tests.

Snapshots are evidence, not productive integration. Failures never replace a
validated snapshot. A prior isolated workbook may be imported once, but only
if its source rows and summary prove the expected store, date and quality.
"""

from datetime import datetime
from pathlib import Path

import pandas as pd

from main import COLUMNS
from scraper.io_utils import atomic_output_path

CONTRACT = "chedraui-directory-ui-v3"


def _boolean(value) -> bool:
    return value is True or str(value).strip().lower() in {"true", "1"}


def _store_id(value) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip().removesuffix(".0")


def validate_case(frame: pd.DataFrame, case: dict, *, category_id: str,
                  store_id: str, day: str) -> list[str]:
    issues = []
    if not frame.shape[0]:
        return ["sin filas"]
    if str(case.get("category_id") or "") != category_id:
        issues.append("categoría del Resumen no coincide")
    if str(case.get("status") or "").upper() != "COMPLETE":
        issues.append("status no COMPLETE")
    if int(pd.to_numeric(pd.Series([case.get("products")]), errors="coerce").fillna(-1).iloc[0]) != len(frame):
        issues.append("conteo de productos no coincide")
    if case.get("store_context_contract_version") != CONTRACT:
        issues.append("contrato de tienda incorrecto")
    if not _boolean(case.get("store_context_verified")):
        issues.append("Resumen sin tienda verificada")
    if not {"retailer", "store_id", "category_id", "scrape_timestamp",
            "store_context_verified", "is_available", "availability_status",
            "price_current", "sku", "url"}.issubset(frame.columns):
        issues.append("faltan columnas fundamentales")
        return issues
    if set(frame["retailer"].dropna().astype(str)) != {"Chedraui"}:
        issues.append("retailer incorrecto")
    if set(frame["store_id"].map(_store_id)) != {store_id}:
        issues.append("tienda incorrecta")
    if set(frame["category_id"].dropna().astype(str)) != {category_id}:
        issues.append("categoría incorrecta")
    if set(frame["scrape_timestamp"].dropna().astype(str).str[:10]) != {day}:
        issues.append("fecha diferente")
    if not frame["store_context_verified"].map(_boolean).all():
        issues.append("filas sin tienda certificada")
    if not pd.to_numeric(frame["is_available"], errors="coerce").isin([0, 1]).all():
        issues.append("disponibilidad no binaria 1/0")
    if not frame["sku"].fillna("").astype(str).str.strip().ne("").all():
        issues.append("SKU incompletos")
    if not frame["url"].fillna("").astype(str).str.strip().ne("").all():
        issues.append("URL incompletas")
    required_price = ~frame["availability_status"].fillna("UNKNOWN").astype(str).str.upper().eq("UNAVAILABLE")
    current = pd.to_numeric(frame["price_current"], errors="coerce")
    if not current.loc[required_price].gt(0).all():
        issues.append("precios obligatorios incompletos")
    for metric in ("pagination_gaps", "structured_price_missing_pages", "price_order_errors"):
        value = pd.to_numeric(pd.Series([case.get(metric)]), errors="coerce").iloc[0]
        if pd.isna(value) or value != 0:
            issues.append(metric + " != 0")
    if frame.duplicated(subset=["sku", "url"]).any():
        issues.append("SKU+URL duplicados")
    return issues


def persist_case(root: Path, frame: pd.DataFrame, case: dict,
                 *, store_id: str, day: str) -> Path:
    category_id = str(case.get("category_id") or "")
    problems = validate_case(
        frame, case, category_id=category_id,
        store_id=store_id, day=day,
    )
    if problems:
        raise ValueError("Checkpoint Chedraui rechazado: " + "; ".join(problems))
    target = (root / "output" / "checkpoints_validos" / "chedraui_cases"
              / store_id)
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = target / f"{category_id}_{stamp}.xlsx"
    enriched_case = dict(case)
    enriched_case.update({"retailer": "Chedraui", "store_id": store_id})
    control = pd.DataFrame([{
        "status": "ISOLATED_VALIDATED",
        "capture_date": day,
        "retailer": "Chedraui",
        "store_id": store_id,
        "category_id": category_id,
        "products": len(frame),
        "note": "Prueba aislada. No demuestra integración productiva.",
    }])
    with atomic_output_path(path) as temporary:
        with pd.ExcelWriter(temporary, engine="openpyxl") as writer:
            frame.reindex(columns=COLUMNS).to_excel(
                writer, sheet_name="Concentrado", index=False
            )
            pd.DataFrame([enriched_case]).to_excel(
                writer, sheet_name="Resumen", index=False
            )
            control.to_excel(writer, sheet_name="Control", index=False)
    return path


def seed_from_prior_test(root: Path, prior: Path, *,
                         store_id: str, day: str) -> list[Path]:
    """Recover valid cases from the previous mutable isolated workbook."""
    if not prior.is_file():
        return []
    try:
        products = pd.read_excel(prior, sheet_name="Concentrado",
                                 dtype={"sku": str, "store_id": str, "category_id": str})
        summary = pd.read_excel(prior, sheet_name="Resumen", dtype={"category_id": str})
    except (OSError, KeyError, ValueError, ImportError):
        return []
    if products.empty or summary.empty:
        return []
    recovered = []
    for _, item in summary.iterrows():
        row = item.to_dict()
        category_id = str(row.get("category_id") or "").strip()
        if not category_id or str(row.get("status") or "").upper() != "COMPLETE":
            continue
        case_products = products.loc[
            products["category_id"].fillna("").astype(str).eq(category_id)
        ].copy()
        case_dir = (root / "output" / "checkpoints_validos"
                    / "chedraui_cases" / store_id)
        # Do not duplicate the same successful category merely because we
        # run a new isolated test. Existing snapshots are retained.
        if any(case_dir.glob(category_id + "_*.xlsx")):
            continue
        problems = validate_case(
            case_products, row, category_id=category_id,
            store_id=store_id, day=day,
        )
        if not problems:
            recovered.append(persist_case(
                root, case_products, row, store_id=store_id, day=day
            ))
    return recovered



def load_valid_snapshots(root: Path, *, day: str):
    """Load latest independently verified same-day checkpoints for Phase 1.

    Returns {(retailer, category_id, store_id): (rows, summary)}. One
    category failure cannot corrupt another case's successful checkpoint.
    """
    base = root / "output" / "checkpoints_validos" / "chedraui_cases"
    result = {}
    if not base.exists():
        return result
    for path in sorted(base.glob("*/*.xlsx"), reverse=True):
        try:
            frame = pd.read_excel(
                path, sheet_name="Concentrado",
                dtype={"sku": str, "store_id": str, "category_id": str},
            )
            summary = pd.read_excel(
                path, sheet_name="Resumen", dtype={"category_id": str, "store_id": str},
            )
            control = pd.read_excel(path, sheet_name="Control")
        except (OSError, ValueError, KeyError, ImportError):
            continue
        if frame.empty or len(summary) != 1 or len(control) != 1:
            continue
        row = summary.iloc[0].to_dict()
        category_id = str(row.get("category_id") or "")
        store_id = _store_id(row.get("store_id"))
        if not store_id or control.iloc[0].get("status") != "ISOLATED_VALIDATED":
            continue
        if str(control.iloc[0].get("capture_date") or "")[:10] != day:
            continue
        key = ("Chedraui", category_id, store_id)
        if key in result:
            continue
        if validate_case(
            frame, row, category_id=category_id,
            store_id=store_id, day=day,
        ):
            continue
        result[key] = (frame, row)
    return result
