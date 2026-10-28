"""Strict 4-CP x 6-category Soriana partition contract in the productive master.

Legacy Soriana has 6 category cases. The postal variant has 24 CP/category
cases and may only be recognized with explicit, independently checked evidence.
This module never modifies inputs, master files or execution state.
"""
from __future__ import annotations

import pandas as pd

CODES = ("83420", "44220", "54030", "97245")
CATEGORIES = (
    "cuidado-bucal", "limpiadores", "detergentes",
    "afeitado-depilacion-dama", "desodorantes-para-caballero",
    "desodorantes-para-dama",
)
SCOPE = "POSTAL_ROTATION_VERIFIED"
RETAILER = "Soriana"
PHASE = "FASE 2"
EXPECTED = {(cp, cat) for cp in CODES for cat in CATEGORIES}


def postal_text(values: pd.Series) -> pd.Series:
    """Read Excel numeric-looking postcodes without losing identity."""
    return values.fillna("").astype(str).str.strip().str.replace(r"\.0$", "", regex=True)


def is_postal_rotation_summary(summary: pd.DataFrame) -> bool:
    if not {"retailer", "source_phase", "integration_scope"}.issubset(summary):
        return False
    rows = summary.loc[
        summary["retailer"].astype(str).eq(RETAILER)
        & summary["source_phase"].astype(str).eq(PHASE)
    ]
    return (not rows.empty
            and rows["integration_scope"].fillna("").astype(str).eq(SCOPE).any())


def postal_issues(frame: pd.DataFrame, summary: pd.DataFrame, day: str | None = None) -> list[str]:
    """Validate both the 24-case evidence and product location grain.

    For an ordinary six-category legacy Soriana master, callers must not invoke
    this checker: use is_postal_rotation_summary to select the new contract.
    """
    issues: list[str] = []
    required_rows = {
        "retailer", "category_id", "postal_code", "sku", "url",
        "price_current", "scrape_timestamp", "store_id",
    }
    required_summary = {
        "retailer", "source_phase", "integration_scope", "category_id",
        "postal_code", "products", "status",
    }
    if not required_rows.issubset(frame):
        return ["Faltan columnas en Concentrado: " + str(sorted(required_rows - set(frame)))]
    if not required_summary.issubset(summary):
        return ["Faltan columnas en Resumen: " + str(sorted(required_summary - set(summary)))]
    data = frame.loc[frame["retailer"].astype(str).eq(RETAILER)].copy()
    cases = summary.loc[
        summary["retailer"].astype(str).eq(RETAILER)
        & summary["source_phase"].astype(str).eq(PHASE)
    ].copy()
    if len(cases) != len(EXPECTED):
        issues.append(f"Resumen Soriana CP: {len(cases)}/{len(EXPECTED)} casos")
    if data.empty:
        issues.append("No hay productos de Soriana")
        return issues
    if not cases["integration_scope"].fillna("").astype(str).eq(SCOPE).all():
        issues.append("Resumen no certifica el alcance postal en todos los casos")
    if not cases["status"].fillna("").astype(str).str.upper().eq("COMPLETE").all():
        issues.append("Hay categorías no COMPLETE")
    cases["postal_code"] = postal_text(cases["postal_code"])
    data["postal_code"] = postal_text(data["postal_code"])
    cases["category_id"] = cases["category_id"].astype(str)
    data["category_id"] = data["category_id"].astype(str)
    keys = list(zip(cases["postal_code"], cases["category_id"]))
    if set(keys) != EXPECTED or len(keys) != len(set(keys)):
        issues.append("Resumen no tiene exactamente 24 pares CP/categoría únicos")
    product_keys = set(zip(data["postal_code"], data["category_id"]))
    if product_keys != EXPECTED:
        issues.append("Concentrado no tiene exactamente 24 pares CP/categoría")
    if data["store_id"].notna().any():
        issues.append("Soriana postal no demuestra IDs de sucursal")
    if "store_context_verified" in data.columns and data["store_context_verified"].fillna(False).astype(bool).any():
        issues.append("Soriana postal no certifica sucursal física")
    sku = data["sku"].fillna("").astype(str).str.strip()
    url = data["url"].fillna("").astype(str).str.strip()
    if sku.eq("").any() or url.eq("").any():
        issues.append("Faltan SKU/URL de producto")
    if not pd.to_numeric(data["price_current"], errors="coerce").gt(0).all():
        issues.append("Precios nulos o no positivos")
    if data.duplicated(subset=["postal_code", "category_id", "sku", "url"]).any():
        issues.append("SKU/URL duplicados dentro del mismo CP y categoría")
    if (day is not None and not
        data["scrape_timestamp"].astype(str).str.startswith(day).all()):
        issues.append("Concentrado contiene otra fecha")
    if day is not None and "capture_date" in cases:
        if not cases["capture_date"].astype(str).eq(day).all():
            issues.append("Resumen contiene otra fecha")
    if "postal_context_verified" not in cases:
        issues.append("Resumen carece de verificación del CP")
    elif not cases["postal_context_verified"].astype(str).str.lower().isin(["true", "1"]).all():
        issues.append("Hay CP sin verificación")
    if "postal_context_evidence" not in cases:
        issues.append("Resumen carece de evidencia visible del CP")
    else:
        evidence = cases["postal_context_evidence"].fillna("").astype(str)
        if not all(v.startswith(f"header_delivery_cp:{cp}")
                   for v, cp in zip(evidence, cases["postal_code"])):
            issues.append("Evidencia de CP no corresponde al encabezado")
    counts = data.groupby(["postal_code", "category_id"], dropna=False).size()
    if pd.to_numeric(cases["products"], errors="coerce").isna().any():
        issues.append("Resumen contiene conteos inválidos")
    else:
        for entry in cases.itertuples(index=False):
            if int(getattr(entry, "products")) != int(
                counts.get((getattr(entry, "postal_code"), getattr(entry, "category_id")), 0)
            ):
                issues.append("Conteos de CP/categoría difieren entre Resumen y Concentrado")
                break
    if "availability_status" in data:
        states = data["availability_status"].fillna("").astype(str)
        if not states.isin(["AVAILABLE", "UNAVAILABLE"]).all():
            issues.append("Disponibilidad online incompleta")
    return issues


def effective_soriana_cases(frame: pd.DataFrame, summary: pd.DataFrame) -> int:
    """Expected case count for Phase 2 without relaxing legacy acceptance.

    An invalid postal partition is recognized as postal-intent but callers
    must also demand postal_issues(...) == [] to mark its phase COMPLETE.
    """
    return len(EXPECTED) if is_postal_rotation_summary(summary) else len(CATEGORIES)
