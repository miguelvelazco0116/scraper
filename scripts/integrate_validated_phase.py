from __future__ import annotations

import argparse
import json
import shutil
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
from scraper.io_utils import atomic_output_path
from scraper.quality_rules import meets_rate, valid_after_errors

CHECKPOINT_DIR = ROOT / "output" / "checkpoints_validos"
CURRENT_MASTER_POINTER = CHECKPOINT_DIR / "current_master.json"
DIAG_PATH = ROOT / "diagnostics" / "phase_checkpoint" / "last_run.json"

PHASES = {
    1: {
        "label": "FASE 1",
        "source": ROOT / "output" / "concentrado_productivo.xlsx",
        "retailers": {
            "Farmacias del Ahorro",
            "Ibarra Mayoreo",
            "Chedraui",
        },
        "categories": 15,
        "cases": 30,
        "require_fg_names": False,
    },
    2: {
        "label": "FASE 2",
        "source": ROOT / "output" / "concentrado_fase2.xlsx",
        "retailers": {
            "Farmacias Guadalajara",
            "Soriana",
        },
        "categories": 10,
        "require_fg_names": True,
    },
    3: {
        "label": "FASE 3",
        "source": ROOT / "output" / "concentrado_fase3.xlsx",
        "retailers": {
            "Bodega Aurrera",
            "Walmart",
        },
        "categories": 5,
        "require_fg_names": False,
    },
    4: {
        "label": "FASE 4",
        "source": ROOT / "output" / "concentrado_fase4.xlsx",
        "retailers": {
            "Farmacias San Pablo",
            "Farmacias Benavides",
        },
        "categories": 7,
        "require_fg_names": False,
    },
    5: {
        "label": "FASE 5",
        "source": ROOT / "output" / "concentrado_fase5.xlsx",
        "retailers": {
            "Farmacias Similares",
            "HEB",
            "Sam's Club",
        },
        "categories": 6,
        "require_fg_names": False,
    },
} 

PHASE3_RETAILER_SOURCES = {
    "Bodega Aurrera": {
        "source": ROOT / "output" / "bodega_aurrera_test.xlsx",
        "categories": 2,
    },
    "Walmart": {
        "source": ROOT / "output" / "walmart_phase3_test.xlsx",
        "categories": 3,
    },
}

PHASE4_RETAILER_SOURCES = {
    "Farmacias San Pablo": {
        "source": ROOT / "output" / "farmacias_san_pablo_test.xlsx",
        "categories": 4,
    },
    "Farmacias Benavides": {
        "source": ROOT / "output" / "benavides_test.xlsx",
        "categories": 3,
    },
}


PHASE5_RETAILER_SOURCES = {
    "Farmacias Similares": {
        "source": ROOT / "output" / "farmacias_similares_test.xlsx",
        "categories": 2,
    },
    "HEB": {
        "source": ROOT / "output" / "heb_phase5_test.xlsx",
        "categories": 2,
    },
    "Sam's Club": {
        "source": ROOT / "output" / "sams_club_phase5_test.xlsx",
        "categories": 2,
    },
}


def _canonical(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    for column in COLUMNS:
        if column not in work.columns:
            work[column] = None
    return work[COLUMNS]


def _read_workbook(path: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
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
    control = pd.read_excel(path, sheet_name="Control")
    return _canonical(concentrated), summary, control


def _read_workbook_optional_control(
    path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
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
    try:
        control = pd.read_excel(path, sheet_name="Control")
    except ValueError:
        control = pd.DataFrame()
    return _canonical(concentrated), summary, control


def _validate_bodega_retailer(
    frame: pd.DataFrame,
    summary: pd.DataFrame,
) -> list[str]:
    notes: list[str] = []

    if frame.empty:
        notes.append("Concentrado Bodega vacío")
    if len(summary) != 2:
        notes.append(f"Bodega categorías {len(summary)}/2")
        return notes

    retailers = set(summary["retailer"].dropna().astype(str))
    if retailers != {"Bodega Aurrera"}:
        notes.append(
            f"Retailers Bodega inesperados: {sorted(retailers)}"
        )

    if not summary["status"].astype(str).str.upper().eq("SUCCESS").all():
        notes.append("Bodega tiene categorías no SUCCESS")

    products = pd.to_numeric(
        summary["products"], errors="coerce"
    ).fillna(-1)
    if int(products.sum()) != len(frame):
        notes.append(
            f"Bodega suma products={int(products.sum())} != filas={len(frame)}"
        )

    for metric in ("sku_complete", "price_complete", "url_complete"):
        if metric not in summary.columns:
            notes.append(f"Bodega sin {metric}")
            continue
        values = pd.to_numeric(
            summary[metric], errors="coerce"
        ).fillna(-2)
        if not all(
            meets_rate(value, total)
            for value, total in zip(values, products)
        ):
            notes.append(f"Bodega {metric} < 90%")

    if "sources_discovered" not in summary.columns or "sources_visited" not in summary.columns:
        notes.append("Bodega sin métricas de fuentes")
    else:
        discovered = pd.to_numeric(
            summary["sources_discovered"], errors="coerce"
        ).fillna(-1)
        visited = pd.to_numeric(
            summary["sources_visited"], errors="coerce"
        ).fillna(-2)
        if not discovered.eq(visited).all():
            notes.append("Bodega fuentes descubiertas != visitadas")

    if "catalogue_gate_ok" not in summary.columns:
        notes.append("Bodega sin catalogue_gate_ok")
    elif not summary["catalogue_gate_ok"].map(bool).all():
        notes.append("Bodega coverage/pagination gate incompleto")

    if "pagination_verified" in summary.columns:
        target = pd.to_numeric(
            summary.get("target_products"),
            errors="coerce",
        )
        no_target = target.isna() | target.eq(0)
        pagination_ok = summary["pagination_verified"].map(bool)
        if not pagination_ok.loc[no_target].all():
            notes.append("Bodega paginación no verificada sin target")

    if "price_validation_errors" not in summary.columns:
        notes.append("Bodega sin price_validation_errors")
    else:
        errors = pd.to_numeric(
            summary["price_validation_errors"], errors="coerce"
        ).fillna(-1)
        if not all(
            valid_after_errors(total, error)
            for total, error in zip(products, errors)
        ):
            notes.append("Bodega validación de precios < 90%")

    return notes


def _validate_walmart_retailer(
    frame: pd.DataFrame,
    summary: pd.DataFrame,
    control: pd.DataFrame,
) -> list[str]:
    notes: list[str] = []

    if frame.empty:
        notes.append("Concentrado Walmart vacío")
    if len(summary) != 3:
        notes.append(f"Walmart categorías {len(summary)}/3")
        return notes

    retailers = set(summary["retailer"].dropna().astype(str))
    if retailers != {"Walmart"}:
        notes.append(
            f"Retailers Walmart inesperados: {sorted(retailers)}"
        )

    if not summary["status"].astype(str).str.upper().eq("COMPLETE").all():
        notes.append("Walmart tiene categorías no COMPLETE")

    if control.empty:
        notes.append("Walmart Control vacío")
    else:
        status = str(control.iloc[0].get("status") or "").strip().upper()
        if status != "PRODUCTIVE":
            notes.append(f"Walmart Control status={status!r}")

    products = pd.to_numeric(
        summary["products"], errors="coerce"
    ).fillna(-1)
    if int(products.sum()) != len(frame):
        notes.append(
            f"Walmart suma products={int(products.sum())} != filas={len(frame)}"
        )

    for metric in (
        "sku_complete",
        "price_complete",
        "url_complete",
        "store_context_complete",
    ):
        if metric not in summary.columns:
            notes.append(f"Walmart sin {metric}")
            continue
        values = pd.to_numeric(
            summary[metric], errors="coerce"
        ).fillna(-2)
        if not all(
            meets_rate(value, total)
            for value, total in zip(values, products)
        ):
            notes.append(f"Walmart {metric} < 90%")

    if "store_context_verified" not in summary.columns:
        notes.append("Walmart sin store_context_verified")
    elif not summary["store_context_verified"].map(bool).all():
        notes.append("Walmart store_context_verified=False")

    if "sources_discovered" not in summary.columns or "sources_visited" not in summary.columns:
        notes.append("Walmart sin métricas de fuentes")
    else:
        discovered = pd.to_numeric(
            summary["sources_discovered"], errors="coerce"
        ).fillna(-1)
        visited = pd.to_numeric(
            summary["sources_visited"], errors="coerce"
        ).fillna(-2)
        if not discovered.eq(visited).all():
            notes.append("Walmart fuentes descubiertas != visitadas")

    if "catalogue_gate_ok" not in summary.columns:
        notes.append("Walmart sin catalogue_gate_ok")
    elif not summary["catalogue_gate_ok"].map(bool).all():
        notes.append("Walmart coverage/pagination gate incompleto")

    if "price_order_errors" not in summary.columns:
        notes.append("Walmart sin price_order_errors")
    else:
        errors = pd.to_numeric(
            summary["price_order_errors"], errors="coerce"
        ).fillna(-1)
        if not all(
            valid_after_errors(total, error)
            for total, error in zip(products, errors)
        ):
            notes.append("Walmart orden de precios válido < 90%")

    if "duplicates_sku_url" not in summary.columns:
        notes.append("Walmart sin duplicates_sku_url")
    else:
        duplicates = pd.to_numeric(
            summary["duplicates_sku_url"], errors="coerce"
        ).fillna(-1)
        if not duplicates.eq(0).all():
            notes.append("Walmart duplicates_sku_url > 0")

    if "sponsored_banner_clicks" in summary.columns:
        sponsored = pd.to_numeric(
            summary["sponsored_banner_clicks"], errors="coerce"
        ).fillna(-1)
        if not sponsored.eq(0).all():
            notes.append("Walmart sponsored_banner_clicks > 0")

    return notes


def _validate_san_pablo_phase(
    frame: pd.DataFrame,
    summary: pd.DataFrame,
) -> list[str]:
    notes: list[str] = []

    if frame.empty:
        notes.append("Concentrado San Pablo vacío")
    if len(summary) != 4:
        notes.append(f"San Pablo categorías {len(summary)}/4")
        return notes

    required_summary = {
        "category_id",
        "target_products",
        "products_extracted",
        "price_complete",
        "status",
        "occ_transport",
    }
    missing = required_summary.difference(summary.columns)
    if missing:
        notes.append(
            "San Pablo Resumen sin columnas: "
            + ", ".join(sorted(missing))
        )
        return notes

    if not summary["status"].astype(str).str.upper().eq("SUCCESS").all():
        notes.append("San Pablo tiene categorías no SUCCESS")

    expected_categories = {
        "descongestionantes",
        "preservativos",
        "enjuagues-bucales",
        "pastas-dentales",
    }
    observed_categories = set(
        summary["category_id"].dropna().astype(str)
    )
    if observed_categories != expected_categories:
        notes.append(
            "Categorías San Pablo inesperadas: "
            f"{sorted(observed_categories)}"
        )

    products = pd.to_numeric(
        summary["products_extracted"], errors="coerce"
    ).fillna(-1)
    targets = pd.to_numeric(
        summary["target_products"], errors="coerce"
    ).fillna(-2)
    prices = pd.to_numeric(
        summary["price_complete"], errors="coerce"
    ).fillna(-3)

    if not all(
        meets_rate(product, target)
        for product, target in zip(products, targets)
    ):
        notes.append("San Pablo cobertura OCC < 90%")
    if not all(
        meets_rate(price, product)
        for price, product in zip(prices, products)
    ):
        notes.append("San Pablo price_complete < 90%")
    if int(products.sum()) != len(frame):
        notes.append(
            f"San Pablo suma products={int(products.sum())} "
            f"!= filas={len(frame)}"
        )

    transports = summary["occ_transport"].fillna("").astype(str)
    if not transports.eq("captured_storefront_response").all():
        notes.append(
            "San Pablo no usó captured_storefront_response en todas "
            "las categorías"
        )

    retailers = set(frame["retailer"].dropna().astype(str))
    if retailers != {"Farmacias San Pablo"}:
        notes.append(
            f"Retailers San Pablo inesperados: {sorted(retailers)}"
        )

    frame_prices = pd.to_numeric(
        frame["price_current"], errors="coerce"
    ).notna().sum()
    if not meets_rate(int(frame_prices), len(frame)):
        notes.append("San Pablo price_current < 90% en Concentrado")

    return notes


def _validate_benavides_phase(
    frame: pd.DataFrame,
    summary: pd.DataFrame,
) -> list[str]:
    notes: list[str] = []

    if frame.empty:
        notes.append("Concentrado Benavides vacío")
    if len(summary) != 3:
        notes.append(f"Benavides categorías {len(summary)}/3")
        return notes

    required = {
        "category_id",
        "target_products",
        "products",
        "sku_complete",
        "price_complete",
        "url_complete",
        "availability_complete",
        "price_order_errors",
    }
    missing = required.difference(summary.columns)
    if missing:
        notes.append(
            "Benavides Resumen sin columnas: "
            + ", ".join(sorted(missing))
        )
        return notes

    expected_categories = {
        "medicamentos-respiratorios",
        "bienestar-sexual",
        "higiene-bucal",
    }
    observed_categories = set(
        summary["category_id"].dropna().astype(str)
    )
    if observed_categories != expected_categories:
        notes.append(
            "Categorías Benavides inesperadas: "
            f"{sorted(observed_categories)}"
        )

    products = pd.to_numeric(
        summary["products"], errors="coerce"
    ).fillna(0)
    targets = pd.to_numeric(
        summary["target_products"], errors="coerce"
    ).fillna(0)

    if int(products.sum()) != len(frame):
        notes.append(
            f"Benavides suma products={int(products.sum())} "
            f"!= filas={len(frame)}"
        )

    for index in summary.index:
        product_count = float(products.loc[index])
        target_count = float(targets.loc[index])
        category_id = str(summary.loc[index, "category_id"])

        if target_count <= 0:
            notes.append(
                f"Benavides {category_id} sin target válido"
            )
            continue

        coverage = product_count / target_count
        if coverage < 0.90:
            notes.append(
                f"Benavides {category_id} coverage={coverage:.4f} < 0.90"
            )

        if product_count <= 0:
            notes.append(
                f"Benavides {category_id} sin productos"
            )
            continue

        for metric in (
            "sku_complete",
            "price_complete",
            "url_complete",
            "availability_complete",
        ):
            complete = float(
                pd.to_numeric(
                    pd.Series([summary.loc[index, metric]]),
                    errors="coerce",
                ).fillna(0).iloc[0]
            )
            rate = complete / product_count
            if rate < 0.90:
                notes.append(
                    f"Benavides {category_id} {metric}="
                    f"{rate:.4f} < 0.90"
                )

        price_errors = float(
            pd.to_numeric(
                pd.Series([summary.loc[index, "price_order_errors"]]),
                errors="coerce",
            ).fillna(product_count).iloc[0]
        )
        price_order_valid_rate = (
            (product_count - price_errors) / product_count
            if product_count > 0
            else 0.0
        )
        if price_order_valid_rate < 0.90:
            notes.append(
                f"Benavides {category_id} price_order_valid_rate="
                f"{price_order_valid_rate:.4f} < 0.90"
            )

    retailers = set(frame["retailer"].dropna().astype(str))
    if retailers != {"Farmacias Benavides"}:
        notes.append(
            f"Retailers Benavides inesperados: {sorted(retailers)}"
        )

    return notes


def _validate_sams_retailer(
    frame: pd.DataFrame,
    summary: pd.DataFrame,
) -> list[str]:
    notes: list[str] = []

    if frame.empty:
        notes.append("Concentrado Sam's Club vacío")
    if len(summary) != 2:
        notes.append(f"Sam's Club categorías {len(summary)}/2")
        return notes

    retailers = set(summary["retailer"].dropna().astype(str))
    if retailers != {"Sam's Club"}:
        notes.append(
            f"Retailers Sam's Club inesperados: {sorted(retailers)}"
        )

    if not summary["status"].astype(str).str.upper().eq("SUCCESS").all():
        notes.append("Sam's Club tiene categorías no SUCCESS")

    products = pd.to_numeric(
        summary["products"], errors="coerce"
    ).fillna(-1)
    if int(products.sum()) != len(frame):
        notes.append(
            f"Sam's Club suma products={int(products.sum())} != filas={len(frame)}"
        )

    for metric in ("sku_complete", "price_complete", "url_complete"):
        if metric not in summary.columns:
            notes.append(f"Sam's Club sin {metric}")
            continue
        values = pd.to_numeric(
            summary[metric], errors="coerce"
        ).fillna(-2)
        if not all(
            meets_rate(value, total)
            for value, total in zip(values, products)
        ):
            notes.append(f"Sam's Club {metric} < 90%")

    if "sources_discovered" not in summary.columns or "sources_visited" not in summary.columns:
        notes.append("Sam's Club sin métricas de fuentes")
    else:
        discovered = pd.to_numeric(
            summary["sources_discovered"], errors="coerce"
        ).fillna(-1)
        visited = pd.to_numeric(
            summary["sources_visited"], errors="coerce"
        ).fillna(-2)
        if not discovered.eq(visited).all():
            notes.append("Sam's Club fuentes descubiertas != visitadas")

    if "catalogue_gate_ok" not in summary.columns:
        notes.append("Sam's Club sin catalogue_gate_ok")
    elif not summary["catalogue_gate_ok"].map(bool).all():
        notes.append("Sam's Club coverage/pagination gate incompleto")

    if "pagination_verified" in summary.columns:
        target = pd.to_numeric(
            summary.get("target_products"),
            errors="coerce",
        )
        no_target = target.isna() | target.eq(0)
        pagination_ok = summary["pagination_verified"].map(bool)
        if not pagination_ok.loc[no_target].all():
            notes.append("Sam's Club paginación no verificada sin target")

    if "price_validation_errors" not in summary.columns:
        notes.append("Sam's Club sin price_validation_errors")
    else:
        errors = pd.to_numeric(
            summary["price_validation_errors"], errors="coerce"
        ).fillna(-1)
        if not all(
            valid_after_errors(total, error)
            for total, error in zip(products, errors)
        ):
            notes.append("Sam's Club validación de precios < 90%")

    return notes



def _validate_similares_phase(
    frame: pd.DataFrame,
    summary: pd.DataFrame,
) -> list[str]:
    notes: list[str] = []

    if frame.empty:
        notes.append("Concentrado Farmacias Similares vacío")
    if len(summary) != 2:
        notes.append(
            f"Farmacias Similares categorías {len(summary)}/2"
        )
        return notes

    required = {
        "category_id",
        "status",
        "target_products",
        "product_links",
        "discovery_complete",
        "products",
        "sku_complete",
        "price_complete",
        "regular_price_complete",
        "url_complete",
        "detail_errors",
        "promotion_price_errors",
        "blocked_detected",
    }
    missing = required.difference(summary.columns)
    if missing:
        notes.append(
            "Farmacias Similares Resumen sin columnas: "
            + ", ".join(sorted(missing))
        )
        return notes

    expected_categories = {
        "aparato-respiratorio",
        "condones",
    }
    observed_categories = set(
        summary["category_id"].dropna().astype(str)
    )
    if observed_categories != expected_categories:
        notes.append(
            "Categorías Farmacias Similares inesperadas: "
            f"{sorted(observed_categories)}"
        )

    if not summary["status"].astype(str).str.upper().eq("SUCCESS").all():
        notes.append(
            "Farmacias Similares tiene categorías no SUCCESS"
        )

    products = pd.to_numeric(
        summary["products"], errors="coerce"
    ).fillna(0)
    targets = pd.to_numeric(
        summary["target_products"], errors="coerce"
    ).fillna(0)
    links = pd.to_numeric(
        summary["product_links"], errors="coerce"
    ).fillna(0)

    if int(products.sum()) != len(frame):
        notes.append(
            "Farmacias Similares suma products="
            f"{int(products.sum())} != filas={len(frame)}"
        )

    for index in summary.index:
        category_id = str(summary.loc[index, "category_id"])
        product_count = float(products.loc[index])
        target_count = float(targets.loc[index])
        link_count = float(links.loc[index])

        if target_count <= 0:
            notes.append(
                f"Farmacias Similares {category_id} sin target válido"
            )
            continue

        if not meets_rate(product_count, target_count):
            notes.append(
                f"Farmacias Similares {category_id} cobertura < 90%"
            )
        if not meets_rate(link_count, target_count):
            notes.append(
                f"Farmacias Similares {category_id} discovery < 90%"
            )
        if not bool(summary.loc[index, "discovery_complete"]):
            notes.append(
                f"Farmacias Similares {category_id} "
                "discovery_complete=False"
            )
        if bool(summary.loc[index, "blocked_detected"]):
            notes.append(
                f"Farmacias Similares {category_id} bloqueado"
            )

        if product_count <= 0:
            notes.append(
                f"Farmacias Similares {category_id} sin productos"
            )
            continue

        for metric in (
            "sku_complete",
            "price_complete",
            "regular_price_complete",
            "url_complete",
        ):
            complete = float(
                pd.to_numeric(
                    pd.Series([summary.loc[index, metric]]),
                    errors="coerce",
                ).fillna(0).iloc[0]
            )
            if not meets_rate(complete, product_count):
                notes.append(
                    f"Farmacias Similares {category_id} "
                    f"{metric} < 90%"
                )

        for metric in (
            "detail_errors",
            "promotion_price_errors",
        ):
            errors = float(
                pd.to_numeric(
                    pd.Series([summary.loc[index, metric]]),
                    errors="coerce",
                ).fillna(product_count).iloc[0]
            )
            denominator = (
                product_count + errors
                if metric == "detail_errors"
                else product_count
            )
            if not valid_after_errors(denominator, errors):
                notes.append(
                    f"Farmacias Similares {category_id} "
                    f"{metric} excede 10%"
                )

    retailers = set(frame["retailer"].dropna().astype(str))
    if retailers != {"Farmacias Similares"}:
        notes.append(
            "Retailers Farmacias Similares inesperados: "
            f"{sorted(retailers)}"
        )

    return notes


def _validate_heb_phase(
    frame: pd.DataFrame,
    summary: pd.DataFrame,
) -> list[str]:
    notes: list[str] = []
    expected_categories = {"higiene-dental", "lavanderia"}

    def as_bool(value) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().casefold() in {
            "1", "true", "yes", "si", "sí",
        }

    if frame.empty:
        notes.append("Concentrado HEB vacío")
    if len(summary) != 2:
        notes.append(f"HEB categorías {len(summary)}/2")
        return notes

    required = {
        "category_id",
        "status",
        "target_products",
        "products",
        "sku_complete",
        "price_complete",
        "url_complete",
        "availability_complete",
        "store_context_complete",
        "store_context_verified",
        "store_context_method",
        "store",
        "price_order_errors",
        "structured_responses",
        "dom_rows",
        "catalogue_source",
        "blocked_detected",
    }
    missing = required.difference(summary.columns)
    if missing:
        notes.append(
            "HEB Resumen sin columnas: "
            + ", ".join(sorted(missing))
        )
        return notes

    observed_categories = set(
        summary["category_id"].dropna().astype(str)
    )
    if observed_categories != expected_categories:
        notes.append(
            f"Categorías HEB inesperadas: {sorted(observed_categories)}"
        )

    if not summary["status"].astype(str).str.upper().eq("SUCCESS").all():
        notes.append("HEB tiene categoría no SUCCESS")

    total_reported = 0
    for category_id in sorted(expected_categories):
        rows = summary.loc[
            summary["category_id"].astype(str).eq(category_id)
        ]
        if len(rows) != 1:
            notes.append(
                f"HEB {category_id} resumen filas={len(rows)}/1"
            )
            continue

        row = rows.iloc[0]
        category_frame = frame.loc[
            frame["category_id"].astype(str).eq(category_id)
        ].copy()

        products = float(pd.to_numeric(
            pd.Series([row.get("products")]),
            errors="coerce",
        ).fillna(0).iloc[0])
        target = float(pd.to_numeric(
            pd.Series([row.get("target_products")]),
            errors="coerce",
        ).fillna(0).iloc[0])
        total_reported += int(products)

        if int(products) != len(category_frame):
            notes.append(
                f"HEB {category_id} products={int(products)} "
                f"!= filas={len(category_frame)}"
            )

        if target <= 0:
            notes.append(f"HEB {category_id} sin target válido")
        elif not meets_rate(products, target):
            notes.append(f"HEB {category_id} cobertura < 90%")

        if products > 0:
            for metric in (
                "sku_complete",
                "price_complete",
                "url_complete",
                "availability_complete",
            ):
                complete = float(pd.to_numeric(
                    pd.Series([row.get(metric)]),
                    errors="coerce",
                ).fillna(0).iloc[0])
                if not meets_rate(complete, products):
                    notes.append(
                        f"HEB {category_id} {metric} < 90%"
                    )

            store_complete = float(pd.to_numeric(
                pd.Series([row.get("store_context_complete")]),
                errors="coerce",
            ).fillna(0).iloc[0])
            if int(store_complete) != int(products):
                notes.append(
                    f"HEB {category_id} store_context_complete "
                    f"{int(store_complete)}/{int(products)}"
                )

            price_errors = float(pd.to_numeric(
                pd.Series([row.get("price_order_errors")]),
                errors="coerce",
            ).fillna(products).iloc[0])
            if not valid_after_errors(products, price_errors):
                notes.append(
                    f"HEB {category_id} orden de precios válido < 90%"
                )

        if not as_bool(row.get("store_context_verified")):
            notes.append(
                f"HEB {category_id} store_context_verified=False"
            )

        method = str(row.get("store_context_method") or "").strip()
        if not method:
            notes.append(
                f"HEB {category_id} sin store_context_method"
            )

        store = str(row.get("store") or "").strip()
        if not store or store.casefold() in {"heb online", "heb"}:
            notes.append(
                f"HEB {category_id} sin tienda concreta"
            )

        catalogue_source = str(row.get("catalogue_source") or "")
        if catalogue_source not in {
            "productSearchV3",
            "dom_fallback",
            "productSearchV3+dom_fallback",
        }:
            notes.append(
                f"HEB {category_id} catalogue_source inválido: "
                f"{catalogue_source!r}"
            )

        if (
            int(row.get("structured_responses") or 0) <= 0
            and int(row.get("dom_rows") or 0) <= 0
        ):
            notes.append(
                f"HEB {category_id} sin evidencia de catálogo"
            )

        if as_bool(row.get("blocked_detected")):
            notes.append(
                f"HEB {category_id} blocked_detected=True"
            )

    if total_reported != len(frame):
        notes.append(
            f"HEB suma products={total_reported} != filas={len(frame)}"
        )

    retailers = set(frame["retailer"].dropna().astype(str))
    if retailers != {"HEB"}:
        notes.append(f"Retailers HEB inesperados: {sorted(retailers)}")

    if "is_available" not in frame.columns:
        notes.append("HEB sin is_available")
    elif not frame.empty:
        availability = pd.to_numeric(
            frame["is_available"],
            errors="coerce",
        )
        valid_availability = int(availability.isin([0, 1]).sum())
        if not meets_rate(valid_availability, len(frame)):
            notes.append("HEB disponibilidad binaria < 90%")

    if "store_context_verified" not in frame.columns:
        notes.append("HEB filas sin store_context_verified")
    elif not frame.empty:
        verified = frame["store_context_verified"].map(as_bool)
        if not verified.all():
            notes.append("HEB filas con contexto de tienda no verificado")

    if "store" not in frame.columns:
        notes.append("HEB filas sin store")
    elif not frame.empty:
        stores = {
            str(value).strip()
            for value in frame["store"].dropna()
            if str(value).strip()
            and str(value).strip().casefold() not in {"heb online", "heb"}
        }
        if len(stores) != 1:
            notes.append(
                f"HEB tiendas concretas esperadas=1 observadas={sorted(stores)}"
            )

    if "store_context_method" not in frame.columns:
        notes.append("HEB filas sin store_context_method")
    elif not frame.empty:
        missing_methods = (
            frame["store_context_method"]
            .fillna("")
            .astype(str)
            .str.strip()
            .eq("")
            .sum()
        )
        if int(missing_methods) > 0:
            notes.append(
                f"HEB filas sin store_context_method={int(missing_methods)}"
            )

    return notes


def _control_capture_date(control: pd.DataFrame) -> str | None:
    if control.empty:
        return None

    row = control.iloc[0]
    explicit = str(row.get("capture_date") or "").strip()
    if explicit:
        return explicit

    for field in ("generated_at", "built_at", "updated_at"):
        raw = str(row.get(field) or "").strip()
        if len(raw) >= 10:
            return raw[:10]

    return None


def _frame_capture_dates(frame: pd.DataFrame) -> set[str]:
    if frame.empty or "scrape_timestamp" not in frame.columns:
        return set()

    return set(
        frame["scrape_timestamp"]
        .dropna()
        .astype(str)
        .str.slice(0, 10)
    )


def _validate_phase(
    phase: int,
    frame: pd.DataFrame,
    summary: pd.DataFrame,
    control: pd.DataFrame,
) -> list[str]:
    cfg = PHASES[phase]
    notes: list[str] = []

    if frame.empty:
        notes.append("Concentrado vacío")
    if summary.empty:
        notes.append("Resumen vacío")
        return notes
    if control.empty:
        notes.append("Control vacío")
    else:
        status = str(control.iloc[0].get("status") or "").strip().upper()
        if status != "PRODUCTIVE":
            notes.append(f"Control status={status!r}")

    expected_cases = int(cfg.get("cases", cfg["categories"]))
    if len(summary) != expected_cases:
        notes.append(
            f"Casos {len(summary)}/{expected_cases}"
        )

    if {"retailer", "category_id"}.issubset(summary.columns):
        retailer_categories = int(
            summary[["retailer", "category_id"]]
            .drop_duplicates()
            .shape[0]
        )
        if retailer_categories != int(cfg["categories"]):
            notes.append(
                "Retailer-categorías "
                f"{retailer_categories}/{cfg['categories']}"
            )

    if "status" not in summary.columns:
        notes.append("Resumen sin status")
    elif not summary["status"].astype(str).str.upper().eq("COMPLETE").all():
        notes.append("Existe al menos una categoría no COMPLETE")

    retailers = set(summary["retailer"].dropna().astype(str))
    if retailers != cfg["retailers"]:
        notes.append(
            f"Retailers {sorted(retailers)} != {sorted(cfg['retailers'])}"
        )

    if "products" not in summary.columns:
        notes.append("Resumen sin products")
    else:
        reported = int(
            pd.to_numeric(summary["products"], errors="coerce")
            .fillna(0)
            .sum()
        )
        if reported != len(frame):
            notes.append(
                f"Suma products={reported} != filas={len(frame)}"
            )

    for metric in ("price_complete", "url_complete"):
        if metric not in summary.columns:
            notes.append(f"Resumen sin {metric}")
            continue

        products = pd.to_numeric(
            summary["products"], errors="coerce"
        ).fillna(-2)

        if phase == 1 and metric == "price_complete":
            chedraui_mask = (
                summary["retailer"].astype(str).eq("Chedraui")
            )
            standard = summary.loc[~chedraui_mask]
            if not standard.empty:
                observed = pd.to_numeric(
                    standard[metric], errors="coerce"
                ).fillna(-1)
                standard_products = pd.to_numeric(
                    standard["products"], errors="coerce"
                ).fillna(-2)
                if not all(
                    meets_rate(value, total)
                    for value, total in zip(observed, standard_products)
                ):
                    notes.append(
                        "price_complete < 90% fuera de Chedraui"
                    )

            chedraui = summary.loc[chedraui_mask]
            required_cols = {
                "price_required",
                "price_required_complete",
            }
            if not required_cols.issubset(chedraui.columns):
                notes.append(
                    "Chedraui sin gate price_required"
                )
            elif not chedraui.empty:
                required = pd.to_numeric(
                    chedraui["price_required"],
                    errors="coerce",
                ).fillna(-1)
                complete = pd.to_numeric(
                    chedraui["price_required_complete"],
                    errors="coerce",
                ).fillna(-2)
                if not all(
                    meets_rate(value, total)
                    for value, total in zip(complete, required)
                    if total > 0
                ):
                    notes.append(
                        "Chedraui price_required < 90%"
                    )
            continue

        observed = pd.to_numeric(
            summary[metric], errors="coerce"
        ).fillna(-1)
        if not all(
            meets_rate(value, total)
            for value, total in zip(observed, products)
        ):
            notes.append(f"{metric} < 90%")

    if phase == 1:
        chedraui = summary.loc[
            summary["retailer"].astype(str).eq("Chedraui")
        ].copy()
        if len(chedraui) != 20:
            notes.append(f"Chedraui casos={len(chedraui)}/20")
        else:
            expected_store_ids = {"232", "173", "255", "76"}
            observed_store_ids = set(
                chedraui["store_id"]
                .dropna()
                .astype(str)
                .str.replace(r"\.0$", "", regex=True)
            )
            if observed_store_ids != expected_store_ids:
                notes.append(
                    "Chedraui store_ids inesperados: "
                    f"{sorted(observed_store_ids)}"
                )

            counts = (
                chedraui.groupby("store_id", dropna=False)
                .size()
                .tolist()
            )
            if sorted(counts) != [5, 5, 5, 5]:
                notes.append(
                    "Chedraui no tiene 5 categorías por tienda"
                )

            if "store_context_verified" not in chedraui.columns:
                notes.append(
                    "Chedraui sin store_context_verified"
                )
            elif not chedraui[
                "store_context_verified"
            ].map(bool).all():
                notes.append(
                    "Chedraui store_context_verified=False"
                )

            for metric in (
                "structured_price_missing_pages",
                "pagination_gaps",
            ):
                if metric not in chedraui.columns:
                    notes.append(f"Chedraui sin {metric}")
                    continue
                values = pd.to_numeric(
                    chedraui[metric],
                    errors="coerce",
                ).fillna(-1)
                if not values.eq(0).all():
                    notes.append(f"Chedraui {metric} > 0")

            if "price_order_errors" not in chedraui.columns:
                notes.append("Chedraui sin price_order_errors")
            else:
                products = pd.to_numeric(
                    chedraui["products"],
                    errors="coerce",
                ).fillna(-1)
                errors = pd.to_numeric(
                    chedraui["price_order_errors"],
                    errors="coerce",
                ).fillna(-1)
                if not all(
                    valid_after_errors(total, error)
                    for total, error in zip(products, errors)
                ):
                    notes.append(
                        "Chedraui orden de precios válido < 90%"
                    )

    if phase == 2 and cfg["require_fg_names"]:
        fg = summary.loc[
            summary["retailer"].astype(str).eq("Farmacias Guadalajara")
        ].copy()
        if len(fg) != 4:
            notes.append(f"Guadalajara categorías={len(fg)}/4")
        elif {
            "product_name_complete",
            "product_name_invalid",
            "products",
        }.issubset(fg.columns):
            products = pd.to_numeric(fg["products"], errors="coerce").fillna(-1)
            complete = pd.to_numeric(
                fg["product_name_complete"], errors="coerce"
            ).fillna(-2)
            invalid = pd.to_numeric(
                fg["product_name_invalid"], errors="coerce"
            ).fillna(-1)
            if not all(
                meets_rate(value, total)
                for value, total in zip(complete, products)
            ):
                notes.append("Guadalajara product_name_complete < 90%")
            if not all(
                valid_after_errors(total, error)
                for total, error in zip(products, invalid)
            ):
                notes.append("Guadalajara nombres válidos < 90%")
        else:
            notes.append("Faltan gates de nombres de Guadalajara")

    if phase == 3:
        walmart = summary.loc[
            summary["retailer"].astype(str).eq("Walmart")
        ].copy()
        bodega = summary.loc[
            summary["retailer"].astype(str).eq("Bodega Aurrera")
        ].copy()

        if len(walmart) != 3:
            notes.append(f"Walmart categorías={len(walmart)}/3")
        else:
            required = {
                "products",
                "store_context_complete",
                "store_context_verified",
                "price_order_errors",
                "duplicates_sku_url",
                "catalogue_gate_ok",
            }
            if not required.issubset(walmart.columns):
                notes.append("Faltan gates Walmart en Fase 3")
            else:
                products = pd.to_numeric(
                    walmart["products"], errors="coerce"
                ).fillna(-1)
                store_complete = pd.to_numeric(
                    walmart["store_context_complete"],
                    errors="coerce",
                ).fillna(-2)
                if not all(
                    meets_rate(value, total)
                    for value, total in zip(store_complete, products)
                ):
                    notes.append(
                        "Walmart store_context_complete < 90%"
                    )
                if not walmart["store_context_verified"].map(bool).all():
                    notes.append(
                        "Walmart store_context_verified=False"
                    )
                price_errors = pd.to_numeric(
                    walmart["price_order_errors"],
                    errors="coerce",
                ).fillna(-1)
                if not all(
                    valid_after_errors(total, error)
                    for total, error in zip(products, price_errors)
                ):
                    notes.append(
                        "Walmart orden de precios válido < 90%"
                    )
                duplicates = pd.to_numeric(
                    walmart["duplicates_sku_url"],
                    errors="coerce",
                ).fillna(-1)
                if not duplicates.eq(0).all():
                    notes.append(
                        "Walmart duplicates_sku_url > 0"
                    )
                if not walmart["catalogue_gate_ok"].map(bool).all():
                    notes.append(
                        "Walmart coverage/pagination gate incompleto"
                    )

        if len(bodega) != 2:
            notes.append(f"Bodega Aurrera categorías={len(bodega)}/2")
        else:
            if "price_validation_errors" not in bodega.columns:
                notes.append(
                    "Falta price_validation_errors de Bodega Aurrera"
                )
            else:
                price_errors = pd.to_numeric(
                    bodega["price_validation_errors"],
                    errors="coerce",
                ).fillna(-1)
                products = pd.to_numeric(
                    bodega["products"],
                    errors="coerce",
                ).fillna(-1)
                if not all(
                    valid_after_errors(total, error)
                    for total, error in zip(products, price_errors)
                ):
                    notes.append(
                        "Bodega Aurrera validación de precios < 90%"
                    )
            if "catalogue_gate_ok" not in bodega.columns:
                notes.append(
                    "Falta catalogue_gate_ok de Bodega Aurrera"
                )
            elif not bodega["catalogue_gate_ok"].map(bool).all():
                notes.append(
                    "Bodega Aurrera coverage/pagination gate incompleto"
                )

    return notes


def _master_path_for_date(capture_date: str) -> Path:
    return (
        ROOT
        / "output"
        / f"concentrado_productivo_total_{capture_date}.xlsx"
    )


def _read_current_master_pointer() -> Path | None:
    if not CURRENT_MASTER_POINTER.exists():
        return None

    try:
        payload = json.loads(
            CURRENT_MASTER_POINTER.read_text(encoding="utf-8")
        )
        raw_path = str(payload.get("master") or "").strip()
        if not raw_path:
            return None
        return Path(raw_path)
    except Exception:
        return None


def _write_current_master_pointer(
    *,
    master_path: Path,
    capture_date: str,
    completed_phases: list[int],
    accepted_partial_phases: list[int] | None = None,
) -> None:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "master": str(master_path),
        "capture_date": capture_date,
        "completed_phases": completed_phases,
        "accepted_partial_phases": accepted_partial_phases or [],
        "updated_at": datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
    }
    with atomic_output_path(CURRENT_MASTER_POINTER) as temporary:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _read_existing_master(
    master_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if not master_path.exists():
        return (
            pd.DataFrame(columns=COLUMNS),
            pd.DataFrame(),
            pd.DataFrame(),
        )
    return _read_workbook(master_path)


def _retailer_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for retailer, subset in frame.groupby("retailer", dropna=False):
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
                    subset["sku"].fillna("").astype(str).str.strip().ne("").sum()
                ),
                "price_complete": int(
                    pd.to_numeric(
                        subset["price_current"], errors="coerce"
                    ).notna().sum()
                ),
                "url_complete": int(
                    subset["url"].fillna("").astype(str).str.strip().ne("").sum()
                ),
            }
        )
    return pd.DataFrame(rows).sort_values("retailer").reset_index(drop=True)


def _retailer_category_count(summary: pd.DataFrame) -> int:
    if summary.empty:
        return 0
    required = {"retailer", "category_id"}
    if not required.issubset(summary.columns):
        return len(summary)
    return int(
        summary[["retailer", "category_id"]]
        .drop_duplicates()
        .shape[0]
    )


def _style(writer: pd.ExcelWriter) -> None:
    wb = writer.book
    for name in ("Concentrado", "Resumen", "Retailers", "Control"):
        ws = wb[name]
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for cell in ws[1]:
            font = copy(cell.font)
            font.bold = True
            cell.font = font
        for cells in ws.columns:
            values = [
                str(cell.value) if cell.value is not None else ""
                for cell in cells[:250]
            ]
            width = min(
                max(max((len(v) for v in values), default=0) + 2, 10),
                42,
            )
            ws.column_dimensions[cells[0].column_letter].width = width


def _write_master(
    master_path: Path,
    concentrated: pd.DataFrame,
    summary: pd.DataFrame,
    control: pd.DataFrame,
) -> None:
    retailers = _retailer_summary(concentrated)
    with atomic_output_path(master_path) as temp:
        with pd.ExcelWriter(temp, engine="openpyxl") as writer:
            concentrated.to_excel(writer, index=False, sheet_name="Concentrado")
            summary.to_excel(writer, index=False, sheet_name="Resumen")
            retailers.to_excel(writer, index=False, sheet_name="Retailers")
            control.to_excel(writer, index=False, sheet_name="Control")
            _style(writer)


def _write_diagnostic(payload: dict[str, Any]) -> None:
    DIAG_PATH.parent.mkdir(parents=True, exist_ok=True)
    DIAG_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Integra una fase productiva validada al concentrado maestro."
    )
    parser.add_argument(
        "--phase",
        type=int,
        choices=[1, 2, 3, 4, 5],
        required=True,
    )
    parser.add_argument(
        "--allow-partial-previous",
        action="store_true",
        help=(
            "Permite continuar cuando una fase previa está explícitamente "
            "marcada PARTIAL_VALIDATED. La fase parcial se registra como "
            "aceptada, pero NO se marca COMPLETE."
        ),
    )
    parser.add_argument(
        "--retailer",
        choices=[
            "Bodega Aurrera",
            "Walmart",
            "Farmacias San Pablo",
            "Farmacias Benavides",
            "Farmacias Similares",
            "HEB",
            "Sam's Club",
        ],
        help=(
            "Integra un retailer validado dentro de Fase 3, Fase 4 "
            "o Fase 5 sin reemplazar las otras particiones de la misma fase."
        ),
    )
    args = parser.parse_args()

    phase = args.phase
    retailer_mode = args.retailer

    if retailer_mode:
        if phase == 3 and retailer_mode not in PHASE3_RETAILER_SOURCES:
            print("ERROR: retailer inválido para Fase 3.")
            return 2
        if phase == 4 and retailer_mode not in PHASE4_RETAILER_SOURCES:
            print("ERROR: retailer inválido para Fase 4.")
            return 2
        if phase == 5 and retailer_mode not in PHASE5_RETAILER_SOURCES:
            print("ERROR: retailer inválido para Fase 5.")
            return 2
        if phase not in (3, 4, 5):
            print(
                "ERROR: --retailer sólo aplica a Fase 3, Fase 4 o Fase 5."
            )
            return 2

    if phase == 4 and not retailer_mode:
        print(
            "ERROR: Fase 4 se integra por retailer. Usa --retailer "
            "'Farmacias San Pablo' o 'Farmacias Benavides'."
        )
        return 2

    if phase == 5 and not retailer_mode:
        print(
            "ERROR: Fase 5 se integra por retailer. Usa --retailer "
            "'Farmacias Similares' o 'HEB'."
        )
        return 2

    cfg = PHASES[phase]
    if phase == 3 and retailer_mode:
        source = PHASE3_RETAILER_SOURCES[retailer_mode]["source"]
    elif phase == 4 and retailer_mode:
        source = PHASE4_RETAILER_SOURCES[retailer_mode]["source"]
    elif phase == 5 and retailer_mode:
        source = PHASE5_RETAILER_SOURCES[retailer_mode]["source"]
    else:
        source = cfg["source"]

    if not source.exists():
        print(f"ERROR: no existe la salida productiva de {cfg['label']}: {source}")
        return 2

    if retailer_mode == "Bodega Aurrera":
        phase_frame, phase_summary, phase_control = (
            _read_workbook_optional_control(source)
        )
        notes = _validate_bodega_retailer(
            phase_frame,
            phase_summary,
        )
    elif retailer_mode == "Walmart":
        phase_frame, phase_summary, phase_control = _read_workbook(source)
        notes = _validate_walmart_retailer(
            phase_frame,
            phase_summary,
            phase_control,
        )
    elif phase == 4 and retailer_mode == "Farmacias San Pablo":
        phase_frame, phase_summary, phase_control = (
            _read_workbook_optional_control(source)
        )
        notes = _validate_san_pablo_phase(
            phase_frame,
            phase_summary,
        )
    elif phase == 4 and retailer_mode == "Farmacias Benavides":
        phase_frame, phase_summary, phase_control = (
            _read_workbook_optional_control(source)
        )
        notes = _validate_benavides_phase(
            phase_frame,
            phase_summary,
        )
    elif phase == 5 and retailer_mode == "Farmacias Similares":
        phase_frame, phase_summary, phase_control = (
            _read_workbook_optional_control(source)
        )
        notes = _validate_similares_phase(
            phase_frame,
            phase_summary,
        )
    elif phase == 5 and retailer_mode == "Sam's Club":
        phase_frame, phase_summary, phase_control = _read_workbook_optional_control(source)
        notes = _validate_sams_retailer(phase_frame, phase_summary)
    elif phase == 5 and retailer_mode == "HEB":
        phase_frame, phase_summary, phase_control = (
            _read_workbook_optional_control(source)
        )
        notes = _validate_heb_phase(
            phase_frame,
            phase_summary,
        )
    else:
        phase_frame, phase_summary, phase_control = _read_workbook(source)
        notes = _validate_phase(
            phase,
            phase_frame,
            phase_summary,
            phase_control,
        )
    if notes:
        _write_diagnostic(
            {
                "status": "REJECTED",
                "phase": phase,
                "source": str(source),
                "notes": notes,
            }
        )
        label = (
            f"{cfg['label']} / {retailer_mode}"
            if retailer_mode
            else cfg["label"]
        )
        print(f"{label} NO integrada:")
        for note in notes:
            print(f"- {note}")
        return 2

    now = datetime.now().astimezone()
    run_id = now.strftime("%Y%m%d_%H%M%S")
    generated_at = now.isoformat(timespec="seconds")

    source_capture_date = _control_capture_date(phase_control)
    row_capture_dates = _frame_capture_dates(phase_frame)

    if source_capture_date is None and len(row_capture_dates) == 1:
        source_capture_date = next(iter(row_capture_dates))

    if source_capture_date is None:
        print(
            f"ERROR: {cfg['label']} no tiene una fecha de captura "
            "determinable."
        )
        return 2

    if row_capture_dates and row_capture_dates != {source_capture_date}:
        print(
            f"ERROR: {cfg['label']} mezcla fechas de captura: "
            f"{sorted(row_capture_dates)}; control={source_capture_date}."
        )
        return 2

    if phase == 1:
        capture_date = source_capture_date
        master_path = _master_path_for_date(capture_date)
        existing_frame = pd.DataFrame(columns=COLUMNS)
        existing_summary = pd.DataFrame()
        completed = []
        accepted_partial = []
    else:
        master_path = _read_current_master_pointer()
        if master_path is None:
            print(
                "ERROR: no existe referencia al concentrado activo. "
                "Primero integra una Fase 1 válida."
            )
            return 2
        capture_date = master_path.stem.rsplit("_", 1)[-1]

        if source_capture_date != capture_date:
            print(
                f"ERROR: {cfg['label']} pertenece a "
                f"{source_capture_date}, pero el master activo es "
                f"{capture_date}."
            )
            return 2

        existing_frame, existing_summary, existing_control = (
            _read_existing_master(master_path)
        )
        if existing_frame.empty or existing_control.empty:
            print("ERROR: primero integra una Fase 1 válida al concentrado.")
            return 2
        completed_raw = str(
            existing_control.iloc[0].get("completed_phases") or ""
        )
        completed = [
            int(x)
            for x in completed_raw.split("|")
            if x.strip().isdigit()
        ]

        accepted_raw = str(
            existing_control.iloc[0].get("accepted_partial_phases") or ""
        )
        accepted_partial = [
            int(x)
            for x in accepted_raw.split("|")
            if x.strip().isdigit()
        ]

        required_previous = list(range(1, phase))

        # Existing explicit acceptances remain valid for later phases. A new
        # acceptance can only be created with the command-line override and
        # only when the master itself says that phase is PARTIAL_VALIDATED.
        if args.allow_partial_previous:
            for item in required_previous:
                if item in completed or item in accepted_partial:
                    continue
                status = str(
                    existing_control.iloc[0].get(
                        f"phase{item}_status"
                    )
                    or ""
                ).strip().upper()
                if status == "PARTIAL_VALIDATED":
                    accepted_partial.append(item)

        accepted_partial = sorted(set(accepted_partial))
        satisfied_previous = set(completed) | set(accepted_partial)
        missing_previous = [
            item
            for item in required_previous
            if item not in satisfied_previous
        ]
        if missing_previous:
            missing_text = ", ".join(
                f"Fase {item}" for item in missing_previous
            )
            print(
                "ERROR: faltan fases previas validadas o aceptadas "
                f"como parciales en el concentrado: {missing_text}."
            )
            return 2

        phase_retailers = (
            {retailer_mode}
            if retailer_mode
            else cfg["retailers"]
        )
        existing_frame = existing_frame.loc[
            ~existing_frame["retailer"].astype(str).isin(phase_retailers)
        ].copy()

        if not existing_summary.empty:
            if retailer_mode and "retailer" in existing_summary.columns:
                existing_summary = existing_summary.loc[
                    existing_summary["retailer"]
                    .astype(str)
                    .ne(retailer_mode)
                ].copy()
            elif "source_phase" in existing_summary.columns:
                existing_summary = existing_summary.loc[
                    existing_summary["source_phase"]
                    .astype(str)
                    .ne(cfg["label"])
                ].copy()

    phase_summary = phase_summary.copy()
    if retailer_mode == "Bodega Aurrera":
        phase_summary["source_status"] = phase_summary["status"].astype(str)
        phase_summary["status"] = "COMPLETE"

    if phase == 4 and retailer_mode == "Farmacias San Pablo":
        phase_summary["source_status"] = phase_summary["status"].astype(str)
        phase_summary["status"] = "COMPLETE"
        phase_summary["retailer"] = "Farmacias San Pablo"
        phase_summary["products"] = pd.to_numeric(
            phase_summary["products_extracted"],
            errors="coerce",
        )

    if phase == 4 and retailer_mode == "Farmacias Benavides":
        if "status" in phase_summary.columns:
            phase_summary["source_status"] = (
                phase_summary["status"].astype(str)
            )
        phase_summary["status"] = "COMPLETE"
        phase_summary["retailer"] = "Farmacias Benavides"
        products_numeric = pd.to_numeric(
            phase_summary["products"], errors="coerce"
        )
        targets_numeric = pd.to_numeric(
            phase_summary["target_products"], errors="coerce"
        )
        phase_summary["coverage"] = (
            products_numeric / targets_numeric
        )
        if "price_order_errors" in phase_summary.columns:
            price_errors = pd.to_numeric(
                phase_summary["price_order_errors"],
                errors="coerce",
            ).fillna(products_numeric)
            phase_summary["price_order_valid_rate"] = (
                (products_numeric - price_errors) / products_numeric
            )

    if phase == 5 and retailer_mode in {"Farmacias Similares", "HEB"}:
        phase_summary["source_status"] = (
            phase_summary["status"].astype(str)
        )
        phase_summary["status"] = "COMPLETE"
        phase_summary["retailer"] = retailer_mode
        products_numeric = pd.to_numeric(
            phase_summary["products"], errors="coerce"
        )
        targets_numeric = pd.to_numeric(
            phase_summary["target_products"], errors="coerce"
        )
        phase_summary["coverage"] = (
            products_numeric / targets_numeric
        )

    if "source_phase" in phase_summary.columns:
        phase_summary["source_phase"] = cfg["label"]
    else:
        phase_summary.insert(0, "source_phase", cfg["label"])

    concentrated = pd.concat(
        [existing_frame, phase_frame],
        ignore_index=True,
    )
    concentrated = _canonical(concentrated)

    summary = pd.concat(
        [existing_summary, phase_summary],
        ignore_index=True,
        sort=False,
    )

    if retailer_mode:
        completed = sorted(set(completed))
    else:
        completed = sorted(set(completed + [phase]))

    phase3_validated_retailers = sorted(
        set(
            concentrated.loc[
                concentrated["retailer"]
                .astype(str)
                .isin(PHASES[3]["retailers"]),
                "retailer",
            ]
            .dropna()
            .astype(str)
            .tolist()
        )
    )

    phase3_summary = (
        summary.loc[
            summary["source_phase"].astype(str).eq("FASE 3")
        ].copy()
        if "source_phase" in summary.columns
        else pd.DataFrame()
    )
    phase3_complete = bool(
        set(phase3_validated_retailers) == PHASES[3]["retailers"]
        and len(phase3_summary) == PHASES[3]["categories"]
        and phase3_summary["status"]
        .astype(str)
        .str.upper()
        .eq("COMPLETE")
        .all()
    )
    if phase3_complete:
        completed = sorted(set(completed + [3]))

    phase4_validated_retailers = sorted(
        set(
            concentrated.loc[
                concentrated["retailer"]
                .astype(str)
                .isin(PHASES[4]["retailers"]),
                "retailer",
            ]
            .dropna()
            .astype(str)
            .tolist()
        )
    )
    phase4_summary = (
        summary.loc[
            summary["source_phase"].astype(str).eq("FASE 4")
        ].copy()
        if "source_phase" in summary.columns
        else pd.DataFrame()
    )
    phase4_complete = bool(
        set(phase4_validated_retailers) == PHASES[4]["retailers"]
        and len(phase4_summary) == PHASES[4]["categories"]
        and phase4_summary["status"]
        .astype(str)
        .str.upper()
        .eq("COMPLETE")
        .all()
    )
    if phase4_complete:
        completed = sorted(set(completed + [4]))
    else:
        completed = [item for item in completed if item != 4]

    phase5_validated_retailers = sorted(
        set(
            concentrated.loc[
                concentrated["retailer"]
                .astype(str)
                .isin(PHASES[5]["retailers"]),
                "retailer",
            ]
            .dropna()
            .astype(str)
            .tolist()
        )
    )
    phase5_summary = (
        summary.loc[
            summary["source_phase"].astype(str).eq("FASE 5")
        ].copy()
        if "source_phase" in summary.columns
        else pd.DataFrame()
    )
    phase5_complete = bool(
        set(phase5_validated_retailers) == PHASES[5]["retailers"]
        and len(phase5_summary) == PHASES[5]["categories"]
        and phase5_summary["status"]
        .astype(str)
        .str.upper()
        .eq("COMPLETE")
        .all()
    )
    if phase5_complete:
        completed = sorted(set(completed + [5]))
    else:
        completed = [item for item in completed if item != 5]

    accepted_partial = sorted(
        item for item in set(accepted_partial)
        if item not in set(completed)
    )

    previous_phase1_status = ""
    if phase != 1 and not existing_control.empty:
        previous_phase1_status = str(
            existing_control.iloc[0].get("phase1_status") or ""
        ).strip().upper()
    phase1_status = (
        "COMPLETE"
        if 1 in completed
        else (
            previous_phase1_status
            if previous_phase1_status
            else "PENDING"
        )
    )
    phase2_status = (
        "COMPLETE"
        if 2 in completed
        else "PENDING"
    )

    control = pd.DataFrame(
        [
            {
                "status": "CHECKPOINT_VALIDATED",
                "updated_at": generated_at,
                "last_phase_added": phase,
                "completed_phases": "|".join(str(x) for x in completed),
                "accepted_partial_phases": "|".join(
                    str(x) for x in accepted_partial
                ),
                "phase1_status": phase1_status,
                "phase2_status": phase2_status,
                "phase3_status": (
                    "COMPLETE"
                    if 3 in completed
                    else (
                        "PARTIAL_VALIDATED"
                        if phase3_validated_retailers
                        else "PENDING"
                    )
                ),
                "phase3_validated_retailers": " | ".join(
                    phase3_validated_retailers
                ),
                "phase4_status": (
                    "COMPLETE"
                    if 4 in completed
                    else (
                        "PARTIAL_VALIDATED"
                        if phase4_validated_retailers
                        else "PENDING"
                    )
                ),
                "phase4_validated_retailers": " | ".join(
                    phase4_validated_retailers
                ),
                "products": len(concentrated),
                "categories": _retailer_category_count(summary),
                "execution_cases": len(summary),
                "retailers": int(concentrated["retailer"].nunique()),
                "phase1_products": int(
                    len(
                        concentrated.loc[
                            concentrated["retailer"].astype(str).isin(
                                PHASES[1]["retailers"]
                            )
                        ]
                    )
                ),
                "phase2_products": int(
                    len(
                        concentrated.loc[
                            concentrated["retailer"].astype(str).isin(
                                PHASES[2]["retailers"]
                            )
                        ]
                    )
                ),
                "phase3_products": int(
                    len(
                        concentrated.loc[
                            concentrated["retailer"].astype(str).isin(
                                PHASES[3]["retailers"]
                            )
                        ]
                    )
                ),
                "phase4_products": int(
                    len(
                        concentrated.loc[
                            concentrated["retailer"].astype(str).isin(
                                PHASES[4]["retailers"]
                            )
                        ]
                    )
                ),
                "phase5_products": int(
                    len(
                        concentrated.loc[
                            concentrated["retailer"].astype(str).isin(
                                PHASES[5]["retailers"]
                            )
                        ]
                    )
                ),
                "phase5_status": (
                    "COMPLETE"
                    if 5 in completed
                    else (
                        "PARTIAL_VALIDATED"
                        if phase5_validated_retailers
                        else "PENDING"
                    )
                ),
                "phase5_validated_retailers": " | ".join(
                    phase5_validated_retailers
                ),
                "capture_date": capture_date,
                "master": str(master_path),
            }
        ]
    )

    _write_master(master_path, concentrated, summary, control)
    _write_current_master_pointer(
        master_path=master_path,
        capture_date=capture_date,
        completed_phases=completed,
        accepted_partial_phases=accepted_partial,
    )

    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    checkpoint_suffix = (
        "_" + retailer_mode.casefold().replace(" ", "_")
        if retailer_mode
        else ""
    )
    checkpoint = (
        CHECKPOINT_DIR
        / f"checkpoint_fase{phase}{checkpoint_suffix}_{run_id}.xlsx"
    )
    shutil.copy2(master_path, checkpoint)

    metadata = {
        "status": "CHECKPOINT_VALIDATED",
        "phase_added": phase,
        "retailer_added": retailer_mode,
        "completed_phases": completed,
        "accepted_partial_phases": accepted_partial,
        "phase1_status": phase1_status,
        "phase2_status": phase2_status,
        "phase3_status": control.iloc[0]["phase3_status"],
        "phase3_validated_retailers": phase3_validated_retailers,
        "phase4_status": control.iloc[0]["phase4_status"],
        "phase4_validated_retailers": phase4_validated_retailers,
        "phase5_status": control.iloc[0]["phase5_status"],
        "phase5_validated_retailers": phase5_validated_retailers,
        "generated_at": generated_at,
        "source": str(source),
        "capture_date": capture_date,
        "master": str(master_path),
        "checkpoint": str(checkpoint),
        "products": len(concentrated),
        "categories": _retailer_category_count(summary),
        "execution_cases": len(summary),
        "retailers": sorted(
            concentrated["retailer"].dropna().astype(str).unique().tolist()
        ),
    }
    checkpoint.with_suffix(".json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_diagnostic(metadata)

    print("=" * 80)
    display_label = (
        f"{cfg['label']} / {retailer_mode}"
        if retailer_mode
        else cfg["label"]
    )
    print(f"{display_label} INTEGRADA AL CONCENTRADO")
    print("=" * 80)
    print(f"Fases completadas : {control.iloc[0]['completed_phases']}")
    print(
        "Parciales aceptadas: "
        f"{control.iloc[0]['accepted_partial_phases'] or 'ninguna'}"
    )
    print(f"Estado Fase 1     : {control.iloc[0]['phase1_status']}")
    print(f"Estado Fase 2     : {control.iloc[0]['phase2_status']}")
    print(f"Estado Fase 3     : {control.iloc[0]['phase3_status']}")
    if phase == 3 and retailer_mode:
        print(
            "Retailers Fase 3  : "
            f"{control.iloc[0]['phase3_validated_retailers']}"
        )
    if phase == 4:
        print(f"Estado Fase 4     : {control.iloc[0]['phase4_status']}")
        print(
            "Retailers Fase 4  : "
            f"{control.iloc[0]['phase4_validated_retailers']}"
        )
    if phase == 5:
        print(f"Estado Fase 5     : {control.iloc[0]['phase5_status']}")
        print(
            "Retailers Fase 5  : "
            f"{control.iloc[0]['phase5_validated_retailers']}"
        )
    print(f"Filas acumuladas  : {len(concentrated)}")
    print(
        "Retailer-categorías: "
        f"{_retailer_category_count(summary)}"
    )
    print(f"Casos ejecutados   : {len(summary)}")
    print(f"Retailers         : {control.iloc[0]['retailers']}")
    print(f"Fecha concentrado : {capture_date}")
    print(f"Master            : {master_path}")
    print(f"Checkpoint        : {checkpoint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
