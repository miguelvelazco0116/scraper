from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
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
    execution_mode: str = "server",
) -> int:
    if execution_mode == "local":
        env = os.environ.copy()
        env["FG_BROWSER_CHANNEL"] = browser_channel
        env["FG_DISABLE_HTTP2"] = "0"
        env["FG_DISABLE_QUIC"] = "0"
        env["FG_GRID_REQUEST_FALLBACK"] = "1"
        env.pop("FG_CDP_URL", None)
        env.pop("FG_USER_AGENT", None)

        command = [
            sys.executable,
            str(ROOT / "main.py"),
            "--retailer",
            "farmacias-guadalajara",
            "--category",
            category,
            "--max-load-more",
            str(max_load_more),
            "--headed",
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env=env,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return 124
        return int(completed.returncode)

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
    unique_skus = int(data.get("unique_skus") or rows)
    unique_urls = int(data.get("unique_urls") or rows)
    expansion = data.get("expansion") or {}
    final_links = int(expansion.get("final_links") or links)

    if links <= 0 or rows <= 0:
        raise RuntimeError(
            f"{category}: extracción vacía (links={links}, rows={rows})"
        )

    if require_complete:
        if not isinstance(target, int) or target <= 0:
            raise RuntimeError(
                f"{category}: no se pudo determinar target_products; "
                "no es posible certificar catálogo completo"
            )

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

        if unique_skus < target:
            raise RuntimeError(
                f"{category}: SKUs únicos incompletos "
                f"(target={target}, unique_skus={unique_skus})"
            )

        if unique_urls < target:
            raise RuntimeError(
                f"{category}: URLs únicas incompletas "
                f"(target={target}, unique_urls={unique_urls})"
            )
    else:
        coverage = (rows / target) if isinstance(target, int) and target > 0 else None

    return {
        "target": target,
        "links": links,
        "final_links": final_links,
        "rows": rows,
        "unique_skus": unique_skus,
        "unique_urls": unique_urls,
        "coverage": coverage,
        "stop_reason": expansion.get("stop_reason"),
        "captured_responses": expansion.get("captured_responses"),
    }


def _restore_consolidated(backup: Path, had_consolidated: bool) -> None:
    if had_consolidated and backup.exists():
        CONSOLIDATED.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, CONSOLIDATED)
    elif not had_consolidated and CONSOLIDATED.exists():
        CONSOLIDATED.unlink()


def _preflight(*, timeout: int, browser_channel: str, execution_mode: str = "server") -> dict:
    """Valida Chrome + navegación + un clic sin dejar muestra parcial."""
    CONTROL_DIR.mkdir(parents=True, exist_ok=True)
    backup = CONTROL_DIR / f"preflight-{uuid.uuid4().hex}.xlsx"
    had_consolidated = CONSOLIDATED.exists()

    if had_consolidated:
        shutil.copy2(CONSOLIDATED, backup)

    try:
        mode_label = "Chrome local" if execution_mode == "local" else "Chrome nativo/CDP"
        print(f"PRECHECK: {mode_label} + Cuidado Bucal 20 -> 40")
        code = _run_submit(
            "cuidado-bucal",
            max_load_more=1,
            timeout=min(timeout, 300),
            browser_channel=browser_channel,
            execution_mode=execution_mode,
        )
        if code == 2:
            raise RuntimeError(
                "Farmacias Guadalajara presentó una verificación/bloqueo. "
                "El proceso se detuvo sin intentar evadirla."
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
        try:
            _restore_consolidated(backup, had_consolidated)
        finally:
            backup.unlink(missing_ok=True)


def _run_category_with_validation(
    category: str,
    *,
    max_load_more: int,
    timeout: int,
    browser_channel: str,
    min_row_coverage: float,
    attempts: int,
    retry_pause: float,
    execution_mode: str = "server",
) -> dict:
    CONTROL_DIR.mkdir(parents=True, exist_ok=True)
    backup = CONTROL_DIR / f"{category}-{uuid.uuid4().hex}.xlsx"
    had_consolidated = CONSOLIDATED.exists()
    if had_consolidated:
        shutil.copy2(CONSOLIDATED, backup)

    last_error: Exception | None = None

    try:
        for attempt in range(1, attempts + 1):
            print(f"Intento {attempt}/{attempts}: {category}")
            code = _run_submit(
                category,
                max_load_more=max_load_more,
                timeout=timeout,
                browser_channel=browser_channel,
                execution_mode=execution_mode,
            )

            if code == 2:
                _restore_consolidated(backup, had_consolidated)
                raise RuntimeError(
                    f"{category}: el sitio presentó una verificación/bloqueo. "
                    "Se detuvo la categoría sin reintentar."
                )

            if code == 0:
                try:
                    validation = _validate_diagnostic(
                        category,
                        require_complete=True,
                        min_row_coverage=min_row_coverage,
                    )
                    return validation
                except Exception as exc:
                    last_error = exc
                    print(f"Validación incompleta [{category}]: {exc}")
            else:
                last_error = RuntimeError(
                    f"{category}: scraper terminó con exit_code={code}"
                )
                print(last_error)

            # Nunca dejamos una muestra parcial en el concentrado entre intentos.
            _restore_consolidated(backup, had_consolidated)

            if attempt < attempts:
                print(
                    f"Reintento conservador en {retry_pause:.0f}s "
                    "(sin aumentar concurrencia ni volumen de requests)..."
                )
                time.sleep(retry_pause)

        raise RuntimeError(
            f"{category}: no se logró una descarga completa después de "
            f"{attempts} intento(s). Último error: {last_error}"
        )
    except Exception:
        _restore_consolidated(backup, had_consolidated)
        raise
    finally:
        backup.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Ejecuta y valida Farmacias Guadalajara vía Task Scheduler "
            "+ Google Chrome nativo/CDP"
        )
    )
    parser.add_argument("--category", default="all")
    parser.add_argument("--max-load-more", type=int, default=100)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--execution-mode", choices=["local", "server"], default="server")
    parser.add_argument("--browser-channel", default=None)
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Omite la validación automática 20 -> 40",
    )
    parser.add_argument(
        "--min-row-coverage",
        type=float,
        default=1.0,
        help="Cobertura mínima filas/target; producción usa 1.0 (cobertura completa)",
    )
    parser.add_argument(
        "--attempts",
        type=int,
        default=2,
        help="Máximo de intentos por categoría. Un bloqueo nunca se reintenta.",
    )
    parser.add_argument(
        "--retry-pause",
        type=float,
        default=10.0,
        help="Pausa en segundos antes de un reintento por fallo transitorio.",
    )
    args = parser.parse_args()

    if not 0 < args.min_row_coverage <= 1:
        parser.error("--min-row-coverage debe estar entre 0 y 1")
    if args.attempts < 1 or args.attempts > 3:
        parser.error("--attempts debe estar entre 1 y 3")
    if args.retry_pause < 0:
        parser.error("--retry-pause no puede ser negativo")
    if args.browser_channel is None:
        args.browser_channel = (
            "chrome" if args.execution_mode == "local" else "chrome-cdp"
        )

    expected_channel = (
        "chrome" if args.execution_mode == "local" else "chrome-cdp"
    )
    if args.browser_channel.casefold() != expected_channel:
        parser.error(
            f"Modo {args.execution_mode}: usa --browser-channel {expected_channel}"
        )

    categories = CATEGORIES if args.category == "all" else [args.category]
    invalid = [x for x in categories if x not in CATEGORIES]
    if invalid:
        raise SystemExit(f"Categoría no válida: {invalid[0]}")

    if not args.skip_preflight:
        try:
            result = _preflight(
                timeout=args.timeout,
                browser_channel=args.browser_channel,
                execution_mode=args.execution_mode,
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

        try:
            validation = _run_category_with_validation(
                category,
                max_load_more=args.max_load_more,
                timeout=args.timeout,
                browser_channel=args.browser_channel,
                min_row_coverage=args.min_row_coverage,
                attempts=args.attempts,
                retry_pause=args.retry_pause,
                execution_mode=args.execution_mode,
            )
        except Exception as exc:
            print(f"ERROR [{category}]: {exc}")
            results.append((category, "ERROR", None))
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
            f"unique_skus={validation['unique_skus']} "
            f"coverage={coverage_text}"
        )

    print()
    print("RESUMEN FARMACIAS GUADALAJARA")
    for category, status, validation in results:
        if validation:
            print(
                f"{category}: {status} "
                f"(target={validation['target']}, "
                f"links={validation['links']}, "
                f"rows={validation['rows']}, "
                f"skus={validation['unique_skus']})"
            )
        else:
            print(f"{category}: {status}")

    success = (
        len(results) == len(categories)
        and all(status == "SUCCESS" for _, status, _ in results)
    )
    if success:
        print(f"Concentrado validado al 100%: {CONSOLIDATED}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
