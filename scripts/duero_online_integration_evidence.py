from __future__ import annotations

"""Evidence gate for explicit, scoped Duero public-online-catalogue approval.

This never certifies prices or stock for a postal code. The documented
manual checkout spot-check and delivery eligibility for CP 59690 are
operator evidence, limited to the checked example. Product-level flags
stay store_context_verified=False and postal_code=None.
"""

import json
from pathlib import Path

import pandas as pd

EXPECTED_CATEGORIES = {
    "cuidado-bucal", "desodorantes-y-antitranspirantes",
    "cuidado-facial-y-corporal", "farmacia", "detergentes",
    "cloros-y-blanqueadores",
}
ONLINE_SCOPE = "PUBLIC_ONLINE_CATALOGUE_NO_POSTAL_PRICE_CERTIFICATION"
MANUAL_DELIVERY_CP = "59690"
MANUAL_CONTEXT_NOTE = (
    "Operador confirmó entrega disponible al CP 59690 y coincidencia "
    "de presentación/precio en un producto no identificado. No se "
    "verificaron individualmente 659 precios/stock específicos por CP."
)


def _latest_report(report_dir: Path, prefix: str, day: str) -> tuple[dict, Path]:
    paths = sorted(report_dir.glob(prefix + day.replace("-", "") + "_*.json"))
    if not paths:
        raise ValueError("Duero sin evidencia del día: " + prefix)
    path = paths[-1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"Duero evidencia JSON inválida: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Duero evidencia no es objeto JSON: " + str(path))
    return data, path


def validate_duero_online_evidence(
    frame: pd.DataFrame, summary: pd.DataFrame, day: str,
    report_dir: Path,
) -> dict:
    """Confirm three same-day machine audits before a human-approved import."""
    if len(frame) != 659 or len(summary) != 6:
        raise ValueError(
            "Duero: total distinto del lote auditado 659/6; revisar manualmente"
        )
    if set(summary["category_id"].astype(str)) != EXPECTED_CATEGORIES:
        raise ValueError("Duero: categorías distintas a las seis auditadas")
    if frame["sku"].fillna("").astype(str).str.strip().eq("").any():
        raise ValueError("Duero: SKU vacío")
    if frame["url"].fillna("").astype(str).str.strip().eq("").any():
        raise ValueError("Duero: URL vacía")
    if frame.duplicated(subset=["category_id", "sku", "url"]).any():
        raise ValueError("Duero: variante duplicada en catálogo")
    for col in ("units_per_package", "price_current", "price_per_unit"):
        if not pd.to_numeric(frame[col], errors="coerce").gt(0).all():
            raise ValueError("Duero: " + col + " incompleto o no positivo")
    if not pd.to_numeric(frame["is_available"], errors="coerce").isin([0, 1]).all():
        raise ValueError("Duero: disponibilidad distinta de 1/0")
    if not frame["store_context_verified"].eq(False).all():
        raise ValueError("Duero: no alterar flag de tienda del catálogo público")
    if frame["postal_code"].notna().any():
        raise ValueError("Duero: precio sin CP no puede etiquetarse como CP específico")

    reports = {}
    paths = {}
    for kind, prefix in (
        ("pagination", "duero_paginacion_"),
        ("storefront", "duero_storefront_"),
        ("packaging", "auditoria_duero_fase5_"),
    ):
        reports[kind], paths[kind] = _latest_report(report_dir, prefix, day)

    pagination = reports["pagination"]
    if not str(pagination.get("generated_at") or "").startswith(day):
        raise ValueError("Duero: auditoría de paginación de otra fecha")
    if pagination.get("all_api_paginations_verified") is not True:
        raise ValueError("Duero: paginación API no certificada")
    cat_audits = pagination.get("categories")
    if not isinstance(cat_audits, list) or len(cat_audits) != 6:
        raise ValueError("Duero: 6 auditorías de paginación requeridas")
    pag_by_id = {str(x.get("category_id")): x for x in cat_audits}
    if set(pag_by_id) != EXPECTED_CATEGORIES:
        raise ValueError("Duero: auditoría de paginación de categorías incorrectas")

    storefront = reports["storefront"]
    if not str(storefront.get("generated_at") or "").startswith(day):
        raise ValueError("Duero: cotejo de storefront de otra fecha")
    if storefront.get("all_storefront_counts_match") is not True:
        raise ValueError("Duero: totales de storefront no coinciden con API")
    store_results = storefront.get("storefront_results")
    if not isinstance(store_results, list) or len(store_results) != 6:
        raise ValueError("Duero: cotejos HTML incompletos")
    store_by_id = {str(x.get("category_id")): x for x in store_results}
    if set(store_by_id) != EXPECTED_CATEGORIES:
        raise ValueError("Duero: cotejos HTML de categorías incorrectas")

    packs = reports["packaging"]
    if packs.get("capture_date") != day or packs.get("variants") != 659:
        raise ValueError("Duero: auditoría de empaques no corresponde al lote")
    if (
        packs.get("dual_pagination_verified") is not True
        or packs.get("unit_count_unknown") != 0
        or packs.get("unit_count_conflicts") != 0
        or packs.get("prior_count_without_variant_evidence") != 0
        or packs.get("identified_units_without_unit_price") != 0
        or packs.get("boxes_without_unit_count") != 0
        or packs.get("unit_count_identified") != 659
    ):
        raise ValueError("Duero: empaques no completamente auditados")
    if packs.get("store_context_verified") != 0:
        raise ValueError("Duero: contexto postal certificado sin sustento")

    for _, s in summary.iterrows():
        key = str(s["category_id"])
        p = pag_by_id[key]
        w = store_by_id[key]
        count = int(s["catalogue_products_seen"])
        variants = int(s["products"])
        if not (
            p.get("pagination_verified") is True
            and p.get("independent_match") is True
            and not p.get("errors")
            and p.get("catalogue_products_seen") == count
            and p.get("variant_count_api") == variants
            and isinstance(p.get("terminal_page_limit50"), int)
            and p["terminal_page_limit50"] >= 2
            and isinstance(p.get("terminal_page_limit250"), int)
            and p["terminal_page_limit250"] >= 2
        ):
            raise ValueError("Duero: paginación discrepante en " + key)
        if not (
            w.get("match") is True
            and w.get("http_status") == 200
            and w.get("api_catalogue_products") == count
            and w.get("html_catalogue_products") == count
        ):
            raise ValueError("Duero: HTML no cotejado para " + key)

    return {
        "scope": ONLINE_SCOPE,
        "operator_approved_delivery_cp": MANUAL_DELIVERY_CP,
        "operator_note": MANUAL_CONTEXT_NOTE,
        "postal_price_verified": False,
        "store_context_verified": False,
        "catalogue_products": int(
            pd.to_numeric(summary["catalogue_products_seen"]).sum()
        ),
        "variants": len(frame),
        "evidence_files": {k: str(v) for k, v in paths.items()},
        "capture_date": day,
    }
