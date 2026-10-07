from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main import COLUMNS, CONSOLIDATED_PATH
from scraper.io_utils import atomic_output_path


POLICY_PATH = ROOT / "config" / "engine_policy.yaml"
DIAGNOSTIC_PATH = ROOT / "diagnostics" / "project_healthcheck.json"


def _load_policy() -> dict:
    return yaml.safe_load(
        POLICY_PATH.read_text(encoding="utf-8")
    ) or {}


def _load_master(path: Path):
    if not path.exists():
        return None, ["master ausente"], []

    critical: list[str] = []
    warnings: list[str] = []

    try:
        excel = pd.ExcelFile(path)
    except Exception as exc:
        return None, [f"master ilegible: {type(exc).__name__}: {exc}"], []

    required_sheets = {"Concentrado", "Resumen"}
    missing_sheets = required_sheets.difference(excel.sheet_names)
    if missing_sheets:
        critical.append(
            "hojas faltantes: " + ", ".join(sorted(missing_sheets))
        )

    if "Concentrado" not in excel.sheet_names:
        return None, critical, warnings

    try:
        frame = pd.read_excel(
            path,
            sheet_name="Concentrado",
            dtype={"sku": str, "store_id": str},
        )
    except Exception as exc:
        critical.append(
            f"Concentrado ilegible: {type(exc).__name__}: {exc}"
        )
        return None, critical, warnings

    missing_columns = [column for column in COLUMNS if column not in frame.columns]
    if missing_columns:
        critical.append(
            "columnas faltantes: " + ", ".join(missing_columns)
        )
        return frame, critical, warnings

    if frame.empty:
        critical.append("Concentrado sin filas")
        return frame, critical, warnings

    product_complete = int(
        frame["product"]
        .fillna("")
        .astype(str)
        .str.strip()
        .ne("")
        .sum()
    )
    if product_complete < len(frame):
        critical.append(
            f"product vacío: {len(frame) - product_complete}"
        )

    availability = (
        frame["availability_status"]
        .fillna("UNKNOWN")
        .astype(str)
        .str.strip()
        .str.upper()
        .replace("", "UNKNOWN")
    )
    allowed = {"AVAILABLE", "UNAVAILABLE", "UNKNOWN"}
    invalid_availability = int((~availability.isin(allowed)).sum())
    if invalid_availability:
        critical.append(
            f"availability_status inválido: {invalid_availability}"
        )

    current = pd.to_numeric(
        frame["price_current"],
        errors="coerce",
    )
    regular = pd.to_numeric(
        frame["price_regular"],
        errors="coerce",
    )
    price_required = ~availability.eq("UNAVAILABLE")
    invalid_required_prices = int(
        (
            price_required
            & (
                current.isna()
                | current.le(0)
            )
        ).sum()
    )
    if invalid_required_prices:
        critical.append(
            f"precio requerido inválido: {invalid_required_prices}"
        )

    order_errors = int(
        (
            current.notna()
            & regular.notna()
            & regular.lt(current)
        ).sum()
    )
    if order_errors:
        critical.append(
            f"price_regular < price_current: {order_errors}"
        )

    sku = frame["sku"].fillna("").astype(str).str.strip()
    url = frame["url"].fillna("").astype(str).str.strip()
    identified = sku.ne("") | url.ne("")
    duplicates = int(
        frame.loc[identified].duplicated(
            subset=[
                "retailer",
                "category_id",
                "city",
                "store_id",
                "sku",
                "url",
            ]
        ).sum()
    )
    if duplicates:
        critical.append(
            f"duplicados identificados: {duplicates}"
        )

    missing_timestamp = int(
        frame["scrape_timestamp"]
        .fillna("")
        .astype(str)
        .str.strip()
        .eq("")
        .sum()
    )
    if missing_timestamp:
        warnings.append(
            f"scrape_timestamp vacío: {missing_timestamp}"
        )

    return frame, critical, warnings


def _check_policy(policy: dict) -> tuple[list[dict], list[str], list[str]]:
    retailers = policy.get("retailers") or {}
    rows: list[dict] = []
    critical: list[str] = []
    warnings: list[str] = []

    for retailer, config in retailers.items():
        active = bool(config.get("active"))
        engine = str(config.get("engine") or "")
        profile_dir = config.get("profile_dir")
        profile_exists = None

        if profile_dir:
            profile_path = ROOT / str(profile_dir)
            profile_exists = profile_path.exists()
            if active and "persistent" in engine and not profile_exists:
                warnings.append(
                    f"{retailer}: perfil aún no creado ({profile_dir})"
                )

        category_path = ROOT / "config" / retailer / "categories.yaml"
        categories = None
        enabled = None
        if category_path.exists():
            try:
                payload = yaml.safe_load(
                    category_path.read_text(encoding="utf-8")
                ) or {}
                items = payload.get("categories") or []
                categories = len(items)
                enabled = sum(
                    1
                    for item in items
                    if item.get("enabled", True)
                )
            except Exception as exc:
                critical.append(
                    f"{retailer}: categories.yaml inválido: {exc}"
                )
        elif active:
            critical.append(
                f"{retailer}: config de categorías ausente"
            )

        rows.append(
            {
                "retailer": retailer,
                "active": active,
                "engine": engine,
                "profile_dir": profile_dir,
                "profile_exists": profile_exists,
                "categories": categories,
                "enabled_categories": enabled,
                "minimum_coverage": config.get("minimum_coverage"),
                "on_failure": config.get("on_failure"),
            }
        )

    return rows, critical, warnings


def _summarize_master(frame: pd.DataFrame | None) -> list[dict]:
    if frame is None or frame.empty:
        return []

    out: list[dict] = []
    grouped = frame.groupby(
        ["retailer", "category_id"],
        dropna=False,
    )

    for (retailer, category_id), subset in grouped:
        availability = (
            subset["availability_status"]
            .fillna("UNKNOWN")
            .astype(str)
            .str.upper()
        )
        out.append(
            {
                "retailer": retailer,
                "category_id": category_id,
                "products": len(subset),
                "sku_complete": int(
                    subset["sku"]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                    .ne("")
                    .sum()
                ),
                "price_complete": int(
                    subset["price_current"].notna().sum()
                ),
                "url_complete": int(
                    subset["url"]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                    .ne("")
                    .sum()
                ),
                "available": int(
                    availability.eq("AVAILABLE").sum()
                ),
                "unavailable": int(
                    availability.eq("UNAVAILABLE").sum()
                ),
                "unknown": int(
                    availability.eq("UNKNOWN").sum()
                ),
            }
        )

    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Health check estructural del proyecto scraper."
    )
    parser.add_argument(
        "--master",
        default=str(ROOT / CONSOLIDATED_PATH),
    )
    args = parser.parse_args()

    master_path = Path(args.master)
    policy = _load_policy()

    frame, master_critical, master_warnings = _load_master(
        master_path
    )
    policy_rows, policy_critical, policy_warnings = _check_policy(
        policy
    )

    critical = master_critical + policy_critical
    warnings = master_warnings + policy_warnings

    lock_path = master_path.with_name(master_path.name + ".lock")
    if lock_path.exists():
        warnings.append(
            f"lock del master presente: {lock_path}"
        )

    result = {
        "status": "FAIL" if critical else "PASS",
        "master": str(master_path),
        "master_exists": master_path.exists(),
        "rows": int(len(frame)) if frame is not None else 0,
        "critical": critical,
        "warnings": warnings,
        "engine_policy": policy_rows,
        "master_summary": _summarize_master(frame),
    }

    DIAGNOSTIC_PATH.parent.mkdir(parents=True, exist_ok=True)
    with atomic_output_path(DIAGNOSTIC_PATH) as temporary:
        temporary.write_text(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )

    print("=" * 78)
    print("PROJECT HEALTHCHECK")
    print("=" * 78)
    print(f"Status : {result['status']}")
    print(f"Master : {master_path}")
    print(f"Filas  : {result['rows']}")
    print("")

    if critical:
        print("CRITICAL")
        for item in critical:
            print(f"  - {item}")
        print("")

    if warnings:
        print("WARNINGS")
        for item in warnings:
            print(f"  - {item}")
        print("")

    policy_frame = pd.DataFrame(policy_rows)
    if not policy_frame.empty:
        print("ENGINE POLICY")
        print(
            policy_frame[
                [
                    "retailer",
                    "active",
                    "engine",
                    "profile_exists",
                    "enabled_categories",
                    "minimum_coverage",
                ]
            ].to_string(index=False)
        )

    print("")
    print(f"Diagnostico: {DIAGNOSTIC_PATH}")

    return 0 if not critical else 1


if __name__ == "__main__":
    raise SystemExit(main())
