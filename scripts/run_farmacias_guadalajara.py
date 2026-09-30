from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUBMIT = ROOT / "scripts" / "fg_submit.py"
CONTROL_DIR = ROOT / "control" / "farmacias_guadalajara"
CONSOLIDATED = ROOT / "output" / "concentrado_scraper.xlsx"
DIAGNOSTICS = ROOT / "diagnostics"

CATEGORIES = [
    "cuidado-bucal",
    "lavanderia",
    "preservativos",
    "vias-respiratorias",
]


def _run_submit(
    category: str,
    *,
    max_load_more: int,
    timeout: int,
    browser_channel: str,
) -> int:
    command = [
        sys.executable,
        str(SUBMIT),
        "--category",
        category,
        "--max-load-more",
        str(max_load_more),
        "--browser-channel",
        browser_channel,
        "--timeout",
        str(timeout),
    ]
    completed = subprocess.run(command, cwd=ROOT, check=False)
    return int(completed.returncode)


def _diagnostic(category: str) -> dict:
    path = DIAGNOSTICS / f"farmacias_guadalajara_{category}.json"
    if not path.exists():
        raise RuntimeError(f"No se generó diagnóstico: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(
            f"Diagnóstico inválido para {category}: {type(exc).__name__}: {exc}"
        ) from exc


def _validate_diagnostic(
    category: str,
    *,
    require_complete: bool,
    min_row_coverage: float,
) -> dict:
    data = _diagnostic(category)
    target = data.get("target_products")
    links = int(data.get("product_links") or 0)
    rows = int(data.get("rows") or 0)
    expansion = data.get("expansion") or {}
    final_links = int(expansion.get("final_links") or links)

    if links <= 0 or rows <= 0:
        raise RuntimeError(
            f"{category}: extracción vacía (links={links}, rows={rows})"
        )

    if require_complete and isinstance(target, int) and target > 0:
        if links < target or final_links < target:
            raise RuntimeError(
                f"{category}: cobertura incompleta de links "
                f"(target={target}, links={links}, final_links={final_links})"
            )
        coverage = rows / target
        if coverage < min_row_coverage:
            raise RuntimeError(
                f"{category}: cobertura de filas {coverage:.1%} menor a "
                f"{min_row_coverage:.1%} (rows={rows}, target={target})"
            )
    else:
        coverage = None

    return {
        "target": target,
        "links": links,
        "rows": rows,
        "coverage": coverage,
        "stop_reason": expansion.get("stop_reason"),
        "captured_responses": expansion.get("captured_responses"),
    }


def _preflight(*, timeout: int, browser_channel: str) -> dict:
    """Valida worker + Edge CDP + navegación + un clic sin dejar muestra parcial."""
    CONTROL_DIR.mkdir(parents=True, exist_ok=True)
    backup = CONTROL_DIR / f"preflight-{uuid.uuid4().hex}.xlsx"
    had_consolidated = CONSOLIDATED.exists()

    if had_consolidated:
        shutil.copy2(CONSOLIDATED, backup)

    try:
        print("PRECHECK: Edge nativo/CDP + Cuidado Bucal 20 -> 40")
        code = _run_submit(
            "cuidado-bucal",
            max_load_more=1,
            timeout=min(timeout, 300),
            browser_channel=browser_channel,
        )
        if code != 0:
            raise RuntimeError(f"Preflight terminó con exit_code={code}")

        data = _diagnostic("cuidado-bucal")
        expansion = data.get("expansion") or {}
        links = int(data.get("product_links") or 0)
        rows = int(data.get("rows") or 0)
        initial = int(expansion.get("initial_links") or 0)
        final_links = int(expansion.get("final_links") or links)
        captured = int(expansion.get("captured_responses") or 0)

        if initial < 20 or final_links < 40 or links < 40 or rows < 40:
            raise RuntimeError(
                "Preflight no logró la expansión 20 -> 40 "
                f"(initial={initial}, final={final_links}, links={links}, rows={rows})"
            )
        if captured < 1:
            raise RuntimeError(
                "Preflight no capturó la respuesta nativa Search-UpdateGrid"
            )

        return {
            "initial_links": initial,
            "final_links": final_links,
            "rows": rows,
            "captured_responses": captured,
        }
    finally:
        # El preflight usa el flujo real y por eso main.py actualiza el Excel.
        # Restauramos exactamente el estado previo antes de la corrida completa.
        try:
            if had_consolidated and backup.exists():
                CONSOLIDATED.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, CONSOLIDATED)
            elif not had_consolidated and CONSOLIDATED.exists():
                CONSOLIDATED.unlink()
        finally:
            backup.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Ejecuta y valida Farmacias Guadalajara vía Task Scheduler "
            "+ Edge nativo/CDP"
        )
    )
    parser.add_argument("--category", default="all")
    parser.add_argument("--max-load-more", type=int, default=100)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--browser-channel", default="msedge-cdp")
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Omite la validación automática 20 -> 40",
    )
    parser.add_argument(
        "--min-row-coverage",
        type=float,
        default=0.95,
        help="Cobertura mínima filas/target para aceptar una categoría completa",
    )
    args = parser.parse_args()

    if not 0 < args.min_row_coverage <= 1:
        parser.error("--min-row-coverage debe estar entre 0 y 1")

    categories = CATEGORIES if args.category == "all" else [args.category]
    invalid = [x for x in categories if x not in CATEGORIES]
    if invalid:
        raise SystemExit(f"Categoría no válida: {invalid[0]}")

    if not args.skip_preflight:
        try:
            result = _preflight(
                timeout=args.timeout,
                browser_channel=args.browser_channel,
            )
            print(
                "PRECHECK OK: "
                f"{result['initial_links']} -> {result['final_links']} links, "
                f"{result['rows']} filas"
            )
        except Exception as exc:
            print(f"PRECHECK ERROR: {type(exc).__name__}: {exc}")
            return 3

    results: list[tuple[str, str, dict | None]] = []

    for category in categories:
        print()
        print("=" * 72)
        print(f"Farmacias Guadalajara / {category}")
        print("=" * 72)

        code = _run_submit(
            category,
            max_load_more=args.max_load_more,
            timeout=args.timeout,
            browser_channel=args.browser_channel,
        )
        if code != 0:
            print(f"ERROR [{category}] exit_code={code}")
            results.append((category, "ERROR", None))
            break

        try:
            validation = _validate_diagnostic(
                category,
                require_complete=True,
                min_row_coverage=args.min_row_coverage,
            )
        except Exception as exc:
            print(f"VALIDATION ERROR [{category}]: {exc}")
            results.append((category, "INVALID", None))
            break

        results.append((category, "SUCCESS", validation))
        coverage_text = (
            f"{validation['coverage']:.1%}"
            if validation["coverage"] is not None
            else "n/a"
        )
        print(
            f"VALIDATED [{category}]: "
            f"target={validation['target']} "
            f"links={validation['links']} "
            f"rows={validation['rows']} "
            f"coverage={coverage_text}"
        )

    print()
    print("RESUMEN FARMACIAS GUADALAJARA")
    for category, status, validation in results:
        if validation:
            print(
                f"{category}: {status} "
                f"(links={validation['links']}, rows={validation['rows']}, "
                f"target={validation['target']})"
            )
        else:
            print(f"{category}: {status}")

    success = (
        len(results) == len(categories)
        and all(status == "SUCCESS" for _, status, _ in results)
    )
    if success:
        print(f"Concentrado validado: {CONSOLIDATED}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
