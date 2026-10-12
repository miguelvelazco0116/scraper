from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]

SOURCE = ROOT / "output" / "concentrado_fase2.xlsx"
TARGET = ROOT / "output" / "fase2_muestra_inicial_valida_v2_2026-10-12.xlsx"
META = ROOT / "output" / "fase2_muestra_inicial_valida_v2_2026-10-12.json"

EXPECTED_RETAILERS = {"Farmacias Guadalajara", "Soriana"}
EXPECTED_CATEGORIES = 10


def _fail(message: str) -> int:
    print(f"ERROR: {message}")
    return 2


def main() -> int:
    if not SOURCE.exists():
        return _fail(f"No existe el master productivo: {SOURCE}")

    if TARGET.exists() or META.exists():
        print("BASELINE V2 YA EXISTE - NO SE SOBRESCRIBE")
        print(f"Archivo : {TARGET}")
        print(f"Metadata: {META}")
        return 0

    try:
        concentrated = pd.read_excel(
            SOURCE,
            sheet_name="Concentrado",
            dtype={"sku": str, "store_id": str},
        )
        summary = pd.read_excel(
            SOURCE,
            sheet_name="Resumen",
            dtype={"category_id": str},
        )
        control = pd.read_excel(
            SOURCE,
            sheet_name="Control",
        )
    except Exception as exc:
        return _fail(
            f"No se pudo leer {SOURCE}: {type(exc).__name__}: {exc}"
        )

    if len(summary) != EXPECTED_CATEGORIES:
        return _fail(
            f"Se esperaban {EXPECTED_CATEGORIES} categorías y hay {len(summary)}."
        )

    if "status" not in summary.columns or not summary["status"].eq("COMPLETE").all():
        return _fail("No todas las categorías están COMPLETE.")

    retailers = set(summary["retailer"].dropna().astype(str))
    if retailers != EXPECTED_RETAILERS:
        return _fail(
            f"Retailers inesperados: {sorted(retailers)}"
        )

    reported_products = int(
        pd.to_numeric(summary["products"], errors="coerce").fillna(0).sum()
    )
    if reported_products != len(concentrated):
        return _fail(
            f"Filas {len(concentrated)} != suma de productos {reported_products}."
        )

    for column in ("sku_complete", "price_complete", "url_complete"):
        values = pd.to_numeric(summary[column], errors="coerce").fillna(-1)
        products = pd.to_numeric(summary["products"], errors="coerce").fillna(0)
        if not values.eq(products).all():
            return _fail(f"Gate incompleto en {column}.")

    fg = summary.loc[
        summary["retailer"].astype(str).eq("Farmacias Guadalajara")
    ].copy()
    if len(fg) != 4:
        return _fail(
            f"Farmacias Guadalajara debe tener 4 categorías y tiene {len(fg)}."
        )

    required_fg_columns = {
        "product_name_complete",
        "product_name_invalid",
    }
    missing = required_fg_columns - set(fg.columns)
    if missing:
        return _fail(
            "Faltan gates de nombre Guadalajara: "
            + ", ".join(sorted(missing))
        )

    fg_products = pd.to_numeric(
        fg["products"], errors="coerce"
    ).fillna(0)
    fg_names = pd.to_numeric(
        fg["product_name_complete"], errors="coerce"
    ).fillna(-1)
    fg_invalid = pd.to_numeric(
        fg["product_name_invalid"], errors="coerce"
    ).fillna(-1)

    if not fg_names.eq(fg_products).all():
        return _fail(
            "Farmacias Guadalajara no tiene nombres completos en todas las filas."
        )
    if not fg_invalid.eq(0).all():
        return _fail(
            "Farmacias Guadalajara contiene nombres de producto inválidos."
        )

    product_text = (
        concentrated.loc[
            concentrated["retailer"].astype(str).eq(
                "Farmacias Guadalajara"
            ),
            "product",
        ]
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
        return _fail(
            f"Se detectaron {int(invalid_mask.sum())} nombres inválidos "
            "en las filas de Guadalajara."
        )

    if control.empty:
        return _fail("La hoja Control está vacía.")

    status = str(control.iloc[0].get("status") or "").strip().upper()
    if status != "PRODUCTIVE":
        return _fail(f"Control no está PRODUCTIVE: {status!r}")

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE, TARGET)

    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    payload = {
        "phase": "FASE 2",
        "status": "VALID_CORRECTED_BASELINE_V2",
        "generated_at": generated_at,
        "source": str(SOURCE),
        "baseline": str(TARGET),
        "products": len(concentrated),
        "categories": len(summary),
        "retailers": sorted(retailers),
        "farmacias_guadalajara_products": int(fg_products.sum()),
        "farmacias_guadalajara_product_name_invalid": int(
            fg_invalid.sum()
        ),
        "note": (
            "Baseline corregida versionada después de validar el fix de "
            "nombres de producto de Farmacias Guadalajara. "
            "La baseline original se conserva sin cambios como evidencia histórica."
        ),
    }
    META.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("=" * 72)
    print("BASELINE CORREGIDA FASE 2 CREADA")
    print("=" * 72)
    print(f"Filas      : {len(concentrated)}")
    print(f"Categorías : {len(summary)}")
    print(f"Guadalajara: {int(fg_products.sum())} productos")
    print("Nombres inválidos Guadalajara: 0")
    print(f"Archivo    : {TARGET}")
    print(f"Metadata   : {META}")
    print("")
    print("La baseline original NO fue sobrescrita.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
