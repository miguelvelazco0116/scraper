from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.io_utils import atomic_output_path


LOG_DIR = ROOT / "diagnostics" / "scrapy_validation"
SUMMARY_PATH = LOG_DIR / "summary.csv"

REGRESSION_CASES = [
    ("farmacias-del-ahorro", "congestion-nasal"),
    ("ibarra-mayoreo", "dentifricos-abarrotes"),
]

CANDIDATE_CASES = [
    ("farmacias-similares", "condones"),
    ("farmacias-similares", "aparato-respiratorio"),
    ("farmacias-san-pablo", "descongestionantes"),
    ("farmacias-san-pablo", "preservativos"),
    ("farmacias-san-pablo", "enjuagues-bucales"),
    ("farmacias-san-pablo", "pastas-dentales"),
]


def _field(text: str, label: str) -> str | None:
    match = re.search(
        rf"^{re.escape(label)}\s*:\s*(.*?)\s*$",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    return match.group(1).strip() if match else None


def _as_int(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "None") else None
    except ValueError:
        return None


def _as_float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "None") else None
    except ValueError:
        return None


def run_case(retailer: str, category: str) -> dict:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "run_scrapy.py"),
        "--retailer",
        retailer,
        "--category",
        category,
    ]
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    text = f"{proc.stdout}\n{proc.stderr}"

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{retailer}_{category}.log"
    with atomic_output_path(log_path) as temporary:
        temporary.write_text(
            "$ " + " ".join(cmd) + "\n\n" + text,
            encoding="utf-8",
        )

    status = (_field(text, "Status") or "UNKNOWN").upper()
    finish_reason = _field(text, "Finish reason")
    products = _as_int(_field(text, "Productos"))
    target = _as_int(_field(text, "Target"))
    coverage = _as_float(_field(text, "Cobertura"))
    failed_pdp = _as_int(_field(text, "PDP fallidos"))
    parse_errors = _as_int(_field(text, "Parse errors"))

    accepted = (
        proc.returncode == 0
        and status in {"COMPLETE", "SAMPLE_ACCEPTED"}
    )
    if accepted:
        decision = "SCRAPY_READY"
    elif status == "BLOCKED" or proc.returncode == 2:
        decision = "BROWSER_REQUIRED"
    else:
        decision = "INVESTIGATE"

    return {
        "retailer": retailer,
        "category_id": category,
        "exit_code": proc.returncode,
        "status": status,
        "finish_reason": finish_reason,
        "engine_classification": decision,
        "products": products,
        "target_products": target,
        "coverage": coverage,
        "failed_product_requests": failed_pdp,
        "parse_errors": parse_errors,
        "accepted": accepted,
        "resolved_engine": decision in {
            "SCRAPY_READY",
            "BROWSER_REQUIRED",
        },
        "log": str(log_path.relative_to(ROOT)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Valida secuencialmente spiders Scrapy activos y candidatos "
            "sin modificar el consolidado."
        )
    )
    parser.add_argument(
        "--group",
        choices=["regression", "candidates", "all"],
        default="all",
    )
    args = parser.parse_args()

    cases: list[tuple[str, str]] = []
    if args.group in {"regression", "all"}:
        cases.extend(REGRESSION_CASES)
    if args.group in {"candidates", "all"}:
        cases.extend(CANDIDATE_CASES)

    results: list[dict] = []
    print("=" * 78)
    print("VALIDACION SECUENCIAL SCRAPY")
    print("=" * 78)
    print(f"Grupo: {args.group}")
    print(f"Casos: {len(cases)}")
    print("Consolidado: NO se modifica")
    print("")

    for index, (retailer, category) in enumerate(cases, start=1):
        print(
            f"[{index}/{len(cases)}] {retailer} / {category}",
            flush=True,
        )
        result = run_case(retailer, category)
        results.append(result)
        print(
            "  -> "
            f"{result['status']} "
            f"{result['products']}/{result['target_products']} "
            f"coverage={result['coverage']} "
            f"exit={result['exit_code']}",
            flush=True,
        )

    frame = pd.DataFrame(results)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with atomic_output_path(SUMMARY_PATH) as temporary:
        frame.to_csv(temporary, index=False, encoding="utf-8-sig")

    print("")
    print("=" * 78)
    print("RESUMEN")
    print("=" * 78)
    if not frame.empty:
        print(
            frame[
                [
                    "retailer",
                    "category_id",
                    "status",
                    "engine_classification",
                    "products",
                    "target_products",
                    "coverage",
                    "accepted",
                    "resolved_engine",
                ]
            ].to_string(index=False)
        )
    print(f"\nResumen CSV: {SUMMARY_PATH}")

    if args.group == "regression":
        return 0 if bool(frame["accepted"].all()) else 1
    return 0 if bool(frame["resolved_engine"].all()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
