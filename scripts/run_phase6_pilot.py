"""CLI-only Phase 6 pilot: YZA CP11590 + Farmatodo online catalogue.

This coordinator is deliberately NOT part of the productive pipeline or master.
Probe mode tests five category pages; capture mode checks five actual category
results and isolated workbooks before reporting COMPLETE_PILOT.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import yaml

from scraper.config import load_categories

MANIFEST = ROOT / "config" / "phase6" / "retailers.yaml"
PHASE6_REPORT = ROOT / "diagnostics" / "phase6" / "last_run.json"
RETAILERS = ("farmacias-yza", "farmatodo")
EXPECTED = {
    "farmacias-yza": {
        "categories": {"medicamentos-respiratorio", "higiene-bucal"},
        "report": ROOT / "diagnostics" / "yza" / "last_run.json",
        "workbook": ROOT / "output" / "yza_cp11590_test.xlsx",
        "name": "Farmacias YZA",
    },
    "farmatodo": {
        "categories": {"respiratorios", "cuidado-bucal", "higiene-personal"},
        "report": ROOT / "diagnostics" / "farmatodo" / "last_run.json",
        "workbook": ROOT / "output" / "pilots" / "farmatodo" / "catalogo_online.xlsx",
        "name": "Farmatodo",
    },
}


def local_day() -> str:
    return datetime.now().astimezone().date().isoformat()


def validated_manifest() -> dict:
    obj = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    if obj.get("phase") != 6 or obj.get("status") != "PILOT_CLI":
        raise ValueError("Contrato de piloto Fase 6 incorrecto.")
    items = obj.get("retailers") or []
    if [item.get("id") for item in items] != list(RETAILERS):
        raise ValueError("Fase 6 requiere YZA y Farmatodo en ese orden.")
    catalogues = {
        "farmacias-yza": ROOT / "config" / "yza" / "categories.yaml",
        "farmatodo": ROOT / "config" / "farmatodo" / "categories.yaml",
    }
    for item in items:
        rid = item["id"]
        if set(item.get("categories") or []) != EXPECTED[rid]["categories"]:
            raise ValueError(f"Categorías Fase 6 inválidas: {rid}")
        actual = {cat.id for cat in load_categories(catalogues[rid])}
        if actual != EXPECTED[rid]["categories"]:
            raise ValueError(f"YAML de retailer inconsistente: {rid}")
        if item["name"] != EXPECTED[rid]["name"]:
            raise ValueError(f"Nombre de retailer incorrecto: {rid}")
        if rid == "farmacias-yza" and (
            item.get("postal_mode") != "FIXED"
            or str(item.get("postal_code")) != "11590"
        ):
            raise ValueError("YZA debe verificar CP fijo 11590.")
        if rid == "farmatodo" and (
            item.get("postal_mode") != "NO_VERIFICATION"
            or item.get("postal_code") is not None
        ):
            raise ValueError("Farmatodo no comprueba CP ni sucursal.")
    return obj


def _bools_equal(values, desired: bool) -> bool:
    return all(str(item).strip().lower() == str(desired).lower()
               for item in values)


def validate_capture(retailer_id: str, day: str) -> dict:
    """After a subprocess exits 0, certify date, category & location claims.

    A subprocess exit code alone never certifies the phase.
    """
    config = EXPECTED[retailer_id]
    output, report_path = config["workbook"], config["report"]
    if not report_path.is_file() or not output.is_file():
        raise ValueError(f"{retailer_id}: faltan Excel o reporte de captura.")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("capture_date") != day:
        raise ValueError(f"{retailer_id}: reporte de una fecha diferente.")
    if report.get("master_modified") is not False:
        raise ValueError(f"{retailer_id}: reporte no declara master intacto.")
    if retailer_id == "farmacias-yza":
        if report.get("complete_pilot") is not True or str(report.get("postal_code")) != "11590":
            raise ValueError("YZA: 2 categorías o CP 11590 sin certificar.")
    elif report.get("complete") is not True or report.get("postal_verification_required") is not False:
        raise ValueError("Farmatodo: 3 categorías o alcance postal inválido.")

    summary = pd.read_excel(
        output, sheet_name="Resumen", dtype={"category_id": str, "postal_code": str}
    )
    frame = pd.read_excel(
        output, sheet_name="Concentrado",
        dtype={"sku": str, "category_id": str, "postal_code": str, "store_id": str}
    )
    if frame.empty or len(summary) != len(config["categories"]):
        raise ValueError(f"{retailer_id}: filas o Resumen incompletos.")
    actual = set(summary["category_id"].astype(str))
    if actual != config["categories"]:
        raise ValueError(f"{retailer_id}: categorías inesperadas: {actual}")
    if set(frame["category_id"].astype(str)) != actual:
        raise ValueError(f"{retailer_id}: faltan filas de alguna categoría.")
    if not frame["scrape_timestamp"].astype(str).str.startswith(day).all():
        raise ValueError(f"{retailer_id}: mezcla capturas de otros días.")
    if not frame["retailer"].eq(config["name"]).all():
        raise ValueError(f"{retailer_id}: retailer incorrecto en filas.")
    if (frame["sku"].fillna("").astype(str).str.strip().eq("").any()
        or frame["url"].fillna("").astype(str).str.strip().eq("").any()
        or not pd.to_numeric(frame["price_current"], errors="coerce").gt(0).all()
        or frame.duplicated(subset=["category_id", "sku", "url"]).any()):
        raise ValueError(f"{retailer_id}: SKU, precios, URLs o duplicados inválidos.")
    if "products" not in summary.columns:
        raise ValueError(f"{retailer_id}: conteos de Resumen ausentes.")
    for _, item in summary.iterrows():
        num = len(frame.loc[frame["category_id"] == item["category_id"]])
        if num != int(item["products"]):
            raise ValueError(f"{retailer_id}: conteos inconsistentes en Resumen.")
        expected_status = "SUCCESS" if retailer_id == "farmacias-yza" else "COMPLETE"
        if str(item.get("status")) != expected_status:
            raise ValueError(f"{retailer_id}: categoría no {expected_status}.")
        if str(item.get("capture_date")) != day:
            raise ValueError(f"{retailer_id}: categoría de otro día.")

    if retailer_id == "farmacias-yza":
        if not frame["postal_code"].astype(str).eq("11590").all():
            raise ValueError("YZA: CP diferente de 11590.")
        if not _bools_equal(frame["store_context_verified"], True):
            raise ValueError("YZA: filas sin prueba de CP.")
    else:
        if frame["postal_code"].notna().any() or frame["store_id"].notna().any():
            raise ValueError("Farmatodo: se atribuyó ubicación no certificada.")
        if not _bools_equal(frame["store_context_verified"], False):
            raise ValueError("Farmatodo: contexto postal falsamente certificado.")
    return {
        "retailer": config["name"], "categories": len(summary),
        "products": len(frame), "workbook": str(output),
        "validated_day": day,
        "scope": ("FIXED_CP_11590" if retailer_id == "farmacias-yza"
                  else "ONLINE_CATALOGUE_NO_CP_VERIFICATION"),
    }


def commands_for(mode: str, rid: str, cdp_url: str = "", max_pages: int = 100) -> list[list[str]]:
    py = sys.executable
    if mode not in ("probe", "capture") or rid not in RETAILERS:
        raise ValueError("Modo o retailer no autorizado.")
    if cdp_url and not cdp_url.startswith(("ws://127.0.0.1:", "http://127.0.0.1:", "ws://localhost:", "http://localhost:")):
        raise ValueError("La conexión CDP debe ser al Chrome local.")
    if not 1 <= max_pages <= 200:
        raise ValueError("max-pages fuera de rango.")
    if rid == "farmacias-yza" and not cdp_url:
        raise ValueError(
            "Farmacias YZA ahora requiere Chrome local preparado por el "
            "operador con CP 11590 y conexión CDP."
        )
    # Explicit CDP is a fallback; default is a Playwright-owned visible
    # Chrome process for each isolated diagnostic or capture runner.
    optional_cdp = ["--cdp-url", cdp_url] if cdp_url else []
    if rid == "farmacias-yza":
        if mode == "probe":
            return [[
                py, str(ROOT / "scripts" / "probe_yza.py"),
                "--category", category, *optional_cdp,
            ] for category in ("medicamentos-respiratorio", "higiene-bucal")]
        return [[
            py, str(ROOT / "scripts" / "test_yza.py"),
            "--category", "all", *optional_cdp,
            "--max-pages", str(max_pages),
        ]]
    if mode == "probe":
        return [[
            py, str(ROOT / "scripts" / "test_farmatodo.py"),
            "--category", category, "--probe", "--headed", *optional_cdp,
            "--max-rounds", str(max_pages),
        ] for category in ("respiratorios", "cuidado-bucal", "higiene-personal")]
    return [[
        py, str(ROOT / "scripts" / "test_farmatodo.py"),
        "--category", "all", "--headed", *optional_cdp,
        "--max-rounds", str(max_pages),
    ]]

def main() -> int:
    parser = argparse.ArgumentParser(description="Fase 6 CLI aislada")
    parser.add_argument("--mode", choices=("probe", "capture"), default="probe")
    parser.add_argument("--retailer", choices=("all", *RETAILERS), default="all")
    parser.add_argument("--cdp-url", default="", help="Chrome local preparado por operador (obligatorio para YZA, opcional para Farmatodo).")
    parser.add_argument("--max-pages", type=int, default=100)
    args = parser.parse_args()
    validated_manifest()
    day = local_day()
    ids = RETAILERS if args.retailer == "all" else (args.retailer,)
    if "farmacias-yza" in ids and not args.cdp_url:
        parser.error(
            "YZA requiere una pestaña de Chrome CDP preparada por el "
            "operador con CP 11590. Usa -UseExistingChrome o -CdpUrl "
            "en run_phase6_pilot.ps1."
        )
    phase_report = {
        "phase": 6, "run_date": day, "mode": args.mode,
        "requested_retailers": list(ids), "master_modified": False,
        "browser_mode": "manual_cdp" if args.cdp_url else "automatic_headed_chrome",
        "phase_status": "IN_PROGRESS", "retailers": [],
    }
    success = True
    for rid in ids:
        print(f"\nFASE 6 | {rid} | {args.mode} | {day}", flush=True)
        statuses = []
        for cmd in commands_for(args.mode, rid, args.cdp_url, args.max_pages):
            # shell=False, allowlisted scripts; never execute uploaded commands.
            proc = subprocess.run(cmd, cwd=ROOT, check=False)
            statuses.append(proc.returncode)
        info = {"id": rid, "exit_codes": statuses}
        if any(code != 0 for code in statuses):
            info["status"] = "ERROR"
            success = False
        elif args.mode == "probe":
            info["status"] = "DIAGNOSTIC_OK_NOT_CERTIFIED"
        else:
            try:
                info.update(validate_capture(rid, day))
                info["status"] = "COMPLETE_PILOT"
            except (ValueError, KeyError, OSError, TypeError) as exc:
                info["status"] = "PARTIAL"
                info["error"] = str(exc)
                success = False
        phase_report["retailers"].append(info)
        print(f"FASE 6 | {rid} | {info['status']}", flush=True)

    phase_report["phase_status"] = (
        ("READY_FOR_CAPTURE" if args.mode == "probe" else "COMPLETE_PILOT")
        if success and len(ids) == len(RETAILERS)
        else ("PARTIAL_PILOT" if args.mode == "capture" else "DIAGNOSTIC_PARTIAL")
    )
    PHASE6_REPORT.parent.mkdir(parents=True, exist_ok=True)
    PHASE6_REPORT.write_text(
        json.dumps(phase_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nReporte de Fase 6: {PHASE6_REPORT}")
    print("Master Fases 1-5: SIN CAMBIOS")
    if args.mode == "probe":
        print("El diagnóstico NO certifica una captura completa.")
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())
