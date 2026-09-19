from __future__ import annotations

import argparse
import csv
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIAG_DIR = ROOT / "diagnostics" / "server_connectivity"
WORKBOOK = ROOT / "output" / "concentrado_scraper.xlsx"


@dataclass(frozen=True)
class Case:
    retailer: str
    category: str
    target_url: str
    location_flag: str
    location_value: str


CASES = [
    Case(
        "soriana",
        "cuidado-bucal",
        "https://www.soriana.com/cuidado-personal-y-belleza/cuidado-bucal/",
        "--location",
        "cdmx",
    ),
    Case(
        "chedraui",
        "higiene-bucal",
        "https://www.chedraui.com.mx/cuidado-e-higiene-personal/higiene-bucal",
        "--store",
        "chedraui-polanco",
    ),
    Case(
        "farmacias-guadalajara",
        "preservativos",
        "https://www.farmaciasguadalajara.com/salud-sexual/preservativos",
        "--location",
        "fg-online",
    ),
    Case(
        "farmacias-del-ahorro",
        "enjuagues-bucales",
        "https://www.fahorro.com/cuidado-personal/higiene-bucal/enjuagues-bucales.html",
        "--location",
        "fahorro-online",
    ),
    Case(
        "walmart",
        "cuidado-bucal",
        "https://www.walmart.com.mx/browse/cuidado-personal/cuidado-bucal/264479_950014",
        "--store",
        "sc-toreo",
    ),
]


def classify(exit_code: int, text: str) -> str:
    lower = text.lower()
    if exit_code == 0:
        return "SUCCESS"
    if exit_code == 2 or "blocked:" in lower:
        return "BLOCKED"
    if exit_code == 4 or "store_context_error" in lower:
        return "STORE_CONTEXT_ERROR"
    if exit_code == 5 or "network_unavailable:" in lower:
        return "NETWORK_UNAVAILABLE"
    if exit_code == 3:
        return "EMPTY"
    if exit_code == 124:
        return "TIMEOUT"
    return "ERROR"


def probe_https(url: str, timeout: int = 20) -> tuple[str, str, str]:
    host = urllib.request.urlparse(url).hostname if hasattr(urllib.request, "urlparse") else None
    if host is None:
        from urllib.parse import urlparse

        host = urlparse(url).hostname

    dns = "UNKNOWN"
    try:
        if host:
            addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, 443)})
            dns = ",".join(addresses[:4]) if addresses else "NO_ADDRESS"
    except Exception as exc:
        return "DNS_ERROR", "", f"{type(exc).__name__}: {exc}"

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "Chrome/139.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return "REACHABLE", str(response.status), dns
    except urllib.error.HTTPError as exc:
        return "REACHABLE_HTTP_ERROR", str(exc.code), dns
    except Exception as exc:
        return "UNREACHABLE", "", f"{dns} | {type(exc).__name__}: {exc}"


def run_case(
    case: Case,
    *,
    walmart_storage_state: Path | None,
    depth: int,
    timeout: int,
) -> dict[str, object]:
    network_status, http_code, network_detail = probe_https(case.target_url)

    cmd = [
        sys.executable,
        "main.py",
        "--retailer",
        case.retailer,
        "--category",
        case.category,
        case.location_flag,
        case.location_value,
        "--max-load-more",
        str(depth),
    ]
    walmart_mode = "not_applicable"
    if case.retailer == "walmart":
        if walmart_storage_state is not None:
            cmd.extend(["--storage-state", str(walmart_storage_state)])
            walmart_mode = "storage_state"
        else:
            walmart_mode = "no_session"

    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        elapsed = round(time.monotonic() - started, 1)
        combined = f"{proc.stdout}\n{proc.stderr}"
        status = classify(proc.returncode, combined)
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        elapsed = round(time.monotonic() - started, 1)
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        combined = f"{stdout}\n{stderr}"
        status = "TIMEOUT"
        exit_code = 124

    DIAG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = DIAG_DIR / f"{case.retailer}_{case.category}.log"
    log_path.write_text(
        "\n".join(
            [
                f"$ {' '.join(cmd)}",
                "",
                f"network_status={network_status}",
                f"http_code={http_code}",
                f"network_detail={network_detail}",
                f"scraper_status={status}",
                f"exit_code={exit_code}",
                f"elapsed_seconds={elapsed}",
                f"walmart_mode={walmart_mode}",
                "",
                combined,
            ]
        ),
        encoding="utf-8",
    )

    return {
        "retailer": case.retailer,
        "category": case.category,
        "network_status": network_status,
        "http_code": http_code,
        "network_detail": network_detail,
        "scraper_status": status,
        "exit_code": exit_code,
        "elapsed_seconds": elapsed,
        "walmart_mode": walmart_mode,
        "log": str(log_path.relative_to(ROOT)),
    }


def print_table(rows: list[dict[str, object]]) -> None:
    columns = [
        ("retailer", 24),
        ("network_status", 22),
        ("http_code", 9),
        ("scraper_status", 24),
        ("elapsed_seconds", 8),
    ]
    header = " | ".join(name.ljust(width) for name, width in columns)
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            " | ".join(
                str(row.get(name, ""))[:width].ljust(width)
                for name, width in columns
            )
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prueba de conectividad y smoke test desde el servidor/Jupyter."
    )
    parser.add_argument(
        "--walmart-storage-state",
        help="Ruta opcional a walmart_session.json exportado desde una sesión verificada.",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=1,
        help="Profundidad/páginas del smoke test por retailer (default: 1).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=240,
        help="Timeout por scraper en segundos (default: 240).",
    )
    args = parser.parse_args()

    state = None
    if args.walmart_storage_state:
        state = Path(args.walmart_storage_state).expanduser().resolve()
        if not state.exists():
            raise SystemExit(f"No existe el storage state de Walmart: {state}")

    DIAG_DIR.mkdir(parents=True, exist_ok=True)
    backup = DIAG_DIR / "concentrado_scraper.before_connectivity_test.xlsx"
    had_workbook = WORKBOOK.exists()
    if had_workbook:
        shutil.copy2(WORKBOOK, backup)

    rows: list[dict[str, object]] = []
    try:
        for case in CASES:
            print(f"\nProbando {case.retailer} / {case.category}...")
            result = run_case(
                case,
                walmart_storage_state=state,
                depth=max(1, args.depth),
                timeout=max(30, args.timeout),
            )
            rows.append(result)
            print(
                f"  red={result['network_status']} http={result['http_code']} "
                f"scraper={result['scraper_status']}"
            )
    finally:
        if had_workbook and backup.exists():
            WORKBOOK.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, WORKBOOK)
        elif not had_workbook and WORKBOOK.exists():
            WORKBOOK.unlink()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    csv_path = DIAG_DIR / f"connectivity_matrix_{stamp}.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print("\nMATRIZ FINAL")
    print_table(rows)
    print(f"\nCSV: {csv_path.relative_to(ROOT)}")

    success = sum(1 for row in rows if row["scraper_status"] == "SUCCESS")
    print(f"SUCCESS: {success}/{len(rows)}")

    if state is None:
        print(
            "\nWalmart se probó sin sesión portable. Para validar el flujo completo "
            "sube walmart_session.json al servidor y usa --walmart-storage-state."
        )

    return 0 if success == len(rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
