from __future__ import annotations

"""Read-only, same-run check: live storefront category counts vs Duero API.

Does not select postal codes, change browser state or claim store-specific prices.
Does not modify the master or its Duero capture checkpoint.
"""

import argparse
import json
import re
import time
from collections import Counter
from datetime import datetime
from html import unescape
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "output" / "duero_mayoreo_phase5_test.xlsx"
REPORT_DIR = ROOT / "output" / "reportes_integracion"
HOST = "dueromayoreo.mayoreoenlinea.mx"

def published_count(markup: str) -> tuple[int | None, str, list[int]]:
    """Prefer HTML heading, otherwise agree on every visible count label.

    Historical search-indexed totals can differ; never use them to certify a
    live API capture. Only same-run HTML page responses are accepted.
    """
    headings = re.findall(
        r"(?is)<h[1-6]\b[^>]*>(.*?)</h[1-6]>", markup or ""
    )
    header_counts = []
    for heading in headings:
        label = unescape(re.sub(r"(?s)<[^>]*>", " ", heading))
        label = " ".join(label.split())
        match = re.fullmatch(r"([0-9][0-9,.]{0,8})\s+productos?", label, re.I)
        if match:
            header_counts.append(int(match.group(1).replace(",", "").replace(".", "")))
    if header_counts and len(set(header_counts)) == 1:
        return header_counts[0], "heading", header_counts
    if len(set(header_counts)) > 1:
        return None, "ambiguous_headings", header_counts

    # Restrict fallback to text outside script/style and avoid JS inventory
    # structures that are not actual visible storefront counters.
    plain = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", " ", markup or "")
    plain = unescape(re.sub(r"(?s)<[^>]*>", " ", plain))
    counts = [
        int(raw.replace(",", "").replace(".", ""))
        for raw in re.findall(r"\b([0-9][0-9,.]{0,8})\s+productos?\b", plain, re.I)
    ]
    if counts and len(set(counts)) == 1:
        return counts[0], "body_count", counts
    return None, "missing_or_ambiguous", counts[:30]


def audit_storefront(request, summary: pd.DataFrame, delay: float) -> pd.DataFrame:
    records = []
    for _, row in summary.iterrows():
        category_id = str(row["category_id"])
        api_count = int(row["catalogue_products_seen"])
        url = f"https://{HOST}/collections/{category_id}"
        result = {
            "category_id": category_id,
            "storefront_url": url,
            "api_catalogue_products": api_count,
            "html_catalogue_products": None,
            "count_method": None,
            "match": False,
            "http_status": None,
            "error": None,
        }
        try:
            response = request.get(url, timeout=60_000)
            result["http_status"] = response.status
            if response.status != 200:
                result["error"] = "HTTP " + str(response.status)
            else:
                count, method, seen = published_count(response.text())
                result["count_method"] = method
                result["html_catalogue_products"] = count
                result["match"] = count is not None and count == api_count
                if count is None:
                    result["error"] = "Cuenta HTML ausente o ambigua: " + str(seen)
                elif count != api_count:
                    result["error"] = (
                        f"Diferencia: API={api_count}, storefront={count}"
                    )
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        records.append(result)
        print(
            f"{category_id}: API={api_count} "
            f"HTML={result['html_catalogue_products']} "
            f"match={result['match']} "
            f"status={result['http_status']} "
            f"error={result['error'] or '-'}",
            flush=True,
        )
        if delay:
            time.sleep(delay)
    return pd.DataFrame(records)


def main():
    parser = argparse.ArgumentParser(description="Verificar catálogo público Duero contra HTML.")
    parser.add_argument("--input", type=Path, default=INPUT)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    args = parser.parse_args()
    if args.delay_seconds < 0:
        parser.error("--delay-seconds debe ser >= 0")
    if not args.input.exists():
        parser.error(f"Captura no encontrada: {args.input}")
    summary = pd.read_excel(
        args.input, sheet_name="Resumen", dtype={"category_id": str}
    )
    required = {"category_id", "catalogue_products_seen", "pagination_verified"}
    if required.difference(summary.columns) or len(summary) != 6:
        parser.error("Captura sin 6 categorías y paginación auditada")
    if not summary["pagination_verified"].eq(True).all():
        parser.error("La paginación API aún no está verificada")

    with sync_playwright() as playwright:
        request = playwright.request.new_context(
            user_agent="Mozilla/5.0 (compatible; ProductCatalogAudit/1.0)",
            extra_http_headers={
                "Accept-Language": "es-MX,es;q=0.9",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        try:
            report = audit_storefront(request, summary, args.delay_seconds)
        finally:
            request.dispose()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    base = REPORT_DIR / ("duero_storefront_" + stamp)
    report.to_csv(base.with_suffix(".csv"), encoding="utf-8-sig", index=False)
    all_match = bool(report["match"].eq(True).all())
    base.with_suffix(".json").write_text(json.dumps({
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "all_storefront_counts_match": all_match,
        "postal_code_verified": False,
        "store_context_verified": False,
        "storefront_results": report.to_dict("records"),
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("")
    print("DUERO | CONTEOS PÚBLICOS DE HTML VS API")
    print(report[["category_id", "api_catalogue_products",
                  "html_catalogue_products", "match", "error"]].to_string(index=False))
    print("Conteos storefront confirmados: " + str(all_match))
    print("Contexto de código postal/tarifas: NO VERIFICADO")
    print(f"CSV: {base.with_suffix('.csv')}")
    print(f"JSON: {base.with_suffix('.json')}")
    return 0 if all_match else 2


if __name__ == "__main__":
    raise SystemExit(main())
