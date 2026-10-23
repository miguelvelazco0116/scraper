"""Read-only business metrics based on the dated productive master and its audit."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd

MASTER_GLOB = "concentrado_productivo_total_*.xlsx"
ERROR_PATTERN = re.compile(r"\b(?:ERROR|BLOCKED|WARNING|WARN|PARTIAL|FAILED|TRACEBACK|PENDIENTE|NO VERIFICADO)\b", re.I)
SECRET_PATTERN = re.compile(r"(?i)(password|pwd|secret|token|access[_-]?key)\s*[:=]\s*([^\s;,]+)")

def available_masters(root: Path):
    output = root / "output"
    return sorted(output.glob(MASTER_GLOB), reverse=True) if output.exists() else []

def active_master(root: Path):
    pointer = root / "output" / "checkpoints_validos" / "current_master.json"
    if not pointer.is_file():
        return None, {}
    try:
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        raw = str(payload.get("master") or "").strip()
        path = Path(raw) if raw else None
        if path and not path.is_absolute():
            path = root / path
        return (path if path and path.is_file() else None), payload
    except (OSError, ValueError, TypeError):
        return None, {}


def phase1_master_view(master: Path, inventory: pd.DataFrame,
                       phase_row: pd.Series, master_retailers: pd.DataFrame,
                       day: str) -> pd.DataFrame | None:
    """UI-only view of Phase 1 when the same-day master certifies all cases.

    Never feeds execution/retry decisions. Master case metadata must reconcile
    exactly with the configured store/category inventory and audited totals.
    """
    if (phase_row.get("estado_calculado") != "COMPLETE"
            or not bool(phase_row.get("control_coincide"))
            or int(phase_row.get("casos_complete", -1)) != len(inventory)
            or int(phase_row.get("casos_esperados", -1)) != len(inventory)):
        return None

    sheets = pd.read_excel(
        master, sheet_name=["Resumen", "Control"],
        dtype={"category_id": str, "store_id": str},
    )
    summary = sheets["Resumen"]
    control = sheets["Control"]
    if (control.empty or str(control.iloc[0].get("capture_date") or "")[:10] != day
            or "source_phase" not in summary.columns):
        return None

    phase_cases = summary.loc[
        summary["source_phase"].astype(str).eq("FASE 1")
    ].copy()
    if len(phase_cases) != len(inventory) or not phase_cases["status"].astype(
        str
    ).str.upper().eq("COMPLETE").all():
        return None

    def norm_store(value):
        return "" if pd.isna(value) else str(value).strip().removesuffix(".0")

    def case_key(retailer, category, store):
        name = str(retailer).strip()
        return (
            name,
            str(category).strip(),
            norm_store(store) if name == "Chedraui" else "",
        )

    expected_keys = [
        case_key(row["retailer"], row["categoria"], row["store_id"])
        for _, row in inventory.iterrows()
    ]
    if len(set(expected_keys)) != len(expected_keys):
        return None

    actual = {}
    for _, row in phase_cases.iterrows():
        key = case_key(row["retailer"], row["category_id"], row.get("store_id"))
        count = pd.to_numeric(pd.Series([row.get("products")]), errors="coerce").iloc[0]
        if key in actual or pd.isna(count) or count <= 0 or int(count) != count:
            return None
        actual[key] = int(count)
    if set(actual) != set(expected_keys):
        return None

    retailers = master_retailers.loc[master_retailers["fase"].eq(1)]
    if len(retailers) != inventory["retailer"].nunique():
        return None
    for _, row in retailers.iterrows():
        name = str(row["retailer"])
        reported = sum(count for key, count in actual.items() if key[0] == name)
        if (str(row["estado"]) != "INTEGRADO_COMPLETE"
                or reported != int(row["filas_master"])
                or sum(key[0] == name for key in actual) != int(row["casos_master"])):
            return None
    if sum(actual.values()) != int(phase_row["filas_master"]):
        return None

    view = inventory.copy()
    view["estado"] = "COMPLETE"
    view["productos_validados"] = [
        actual[key] for key in expected_keys
    ]
    # The display reports productive integration, not the capture runner's
    # current skip/retry decisions (which still depend on its own checkpoints).
    view["accion"] = "INTEGRADO"
    view["detalle"] = "Caso COMPLETE certificado en el master productivo del día"
    return view

def inventory_for_master(root: Path, master: Path):
    scripts = str(root / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from audit_master_phases import inventory
    return inventory(master)

def load_master_rows(master: Path):
    return pd.read_excel(master, sheet_name="Concentrado",
                         dtype={"sku": str, "store_id": str, "category_id": str})

def _present(series: pd.Series):
    return series.notna() & series.astype(str).str.strip().ne("")

def _count(part, field, positive=False):
    if field not in part:
        return None
    return (int(pd.to_numeric(part[field], errors="coerce").gt(0).sum())
            if positive else int(_present(part[field]).sum()))

def metrics_by_retailer(frame: pd.DataFrame, expected):
    """Measured completeness, not a substitute for retailer-specific quality gates."""
    rows = []
    for phase, retailers in expected.items():
        for retailer in retailers:
            part = frame.loc[frame["retailer"].fillna("").astype(str).eq(retailer)]
            count = len(part)
            sku, price, url = (_count(part, "sku"),
                               _count(part, "price_current", positive=True),
                               _count(part, "url"))
            availability = (_count(part, "is_available") if "is_available" in part else None)
            if "is_available" in part:
                availability = int(pd.to_numeric(part["is_available"], errors="coerce").isin([0, 1]).sum())
                if availability == 0 and part["is_available"].isna().all():
                    availability = None
            store = (int(part["store_context_verified"].eq(True).sum())
                     if "store_context_verified" in part and part["store_context_verified"].notna().any()
                     else None)
            def pct(value):
                return round(100 * value / count, 1) if count and value is not None else None
            rows.append({
                "fase": phase, "retailer": retailer, "filas": count,
                "sku_pct": pct(sku), "precio_pct": pct(price), "url_pct": pct(url),
                "disponibilidad_pct": pct(availability), "contexto_tienda_pct": pct(store),
                "alcance_online": retailer == "Mayorista Duero",
            })
    return pd.DataFrame(rows)

def quality_findings(frame: pd.DataFrame, expected, limit=500):
    """Informational anomalies only; do not reclassify production status."""
    columns = ["fase", "retailer", "category_id", "store_id", "sku", "product", "tipo", "detalle"]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    base = frame[[col for col in ("retailer", "category_id", "store_id", "sku", "product")
                  if col in frame]].copy()
    phases = {name: phase for phase, retailers in expected.items() for name in retailers}
    base.insert(0, "fase", frame["retailer"].map(phases))
    findings = []
    def add(mask, kind, note):
        subset = base.loc[mask.fillna(False)].copy().head(limit)
        if not subset.empty:
            subset["tipo"], subset["detalle"] = kind, note
            findings.append(subset)
    if "price_current" in frame:
        add(~pd.to_numeric(frame["price_current"], errors="coerce").gt(0),
            "PRECIO", "Precio no positivo o vacío")
    if "sku" in frame:
        add(~_present(frame["sku"]), "SKU", "Identificador ausente (puede ser informativo por retailer)")
    if "url" in frame:
        add(~_present(frame["url"]), "URL", "URL ausente (ver reglas específicas)")
    if "is_available" in frame:
        add(frame["is_available"].notna() &
            ~pd.to_numeric(frame["is_available"], errors="coerce").isin([0, 1]),
            "DISPONIBILIDAD", "Valor provisto distinto de 1/0")
    if "scrape_timestamp" in frame:
        add(~_present(frame["scrape_timestamp"]), "FECHA", "Timestamp ausente")
    return pd.concat(findings, ignore_index=True).head(limit) if findings else pd.DataFrame(columns=columns)

def capture_dates(frame):
    if "scrape_timestamp" not in frame:
        return []
    return sorted(set(frame["scrape_timestamp"].dropna().astype(str).str.slice(0, 10)))

def recent_logs(root: Path, limit=30):
    diag = root / "diagnostics"
    if not diag.is_dir():
        return []
    return sorted((p for p in diag.rglob("*.log") if p.is_file()),
                  key=lambda p: p.stat().st_mtime, reverse=True)[:limit]

def safe_log_tail(path: Path, max_bytes=65536):
    with path.open("rb") as file:
        file.seek(0, 2)
        file.seek(max(0, file.tell() - max_bytes))
        raw = file.read(max_bytes)
    return SECRET_PATTERN.sub(r"\1=[REDACTED]", raw.decode("utf-8-sig", errors="replace"))

def log_events(root: Path, limit=100):
    rows = []
    for path in recent_logs(root, 20):
        for line in reversed(safe_log_tail(path, 40000).splitlines()):
            if ERROR_PATTERN.search(line):
                rows.append({"archivo": str(path.relative_to(root)), "evento": line.strip()[:350]})
                if len(rows) >= limit:
                    return pd.DataFrame(rows)
    return pd.DataFrame(rows, columns=["archivo", "evento"])

def checkpoint_files(root: Path, limit=40):
    directory = root / "output" / "checkpoints_validos"
    columns = ["archivo", "modificado", "tamaño_MB"]
    if not directory.exists():
        return pd.DataFrame(columns=columns)
    files = sorted((p for p in directory.rglob("*.xlsx") if p.is_file()),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    return pd.DataFrame([
        {"archivo": str(p.relative_to(root)),
         "modificado": pd.Timestamp(p.stat().st_mtime, unit="s").isoformat(timespec="seconds"),
         "tamaño_MB": round(p.stat().st_size / (1024 ** 2), 2)}
        for p in files], columns=columns)

def safe_cli_command(root: Path, phase: int, retailer=None):
    """Fixed commands only; no user-provided shell arguments."""
    commands = {
        1: {None: "run_phase1_production.ps1"},
        2: {None: "run_phase2_production.ps1"},
        3: {None: "run_phase3_production.ps1", "Bodega Aurrera": "test_bodega_aurrera.ps1", "Walmart": "test_walmart_phase3.ps1"},
        4: {None: "run_phase4_production.ps1", "Farmacias San Pablo": "run_san_pablo_full.ps1", "Farmacias Benavides": "test_benavides.ps1"},
        5: {None: "run_phase5_production.ps1", "Farmacias Similares": "test_farmacias_similares.ps1",
            "HEB": "test_heb.ps1", "Sam's Club": "test_sams_club.ps1", "Mayorista Duero": "test_duero_mayoreo.ps1"},
    }
    runner = commands.get(phase, {}).get(retailer)
    if not runner:
        return "No hay runner aislado verificado; utiliza el runner de la fase."
    skip = (" -SkipIntegration" if runner in {
        "run_phase2_production.ps1", "run_phase3_production.ps1",
        "run_phase4_production.ps1", "run_phase5_production.ps1", "run_san_pablo_full.ps1"
    } else "")
    return f"cd {root}\n& .\\scripts\\{runner}{skip}"
