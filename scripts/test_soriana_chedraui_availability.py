from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd
from openpyxl.styles import Font

from main import COLUMNS
from scraper.config import load_categories, load_locations
from scraper.retailers.chedraui import (
    ChedrauiBlocked,
    ChedrauiScraper,
    ChedrauiStoreContextError,
)
from scraper.retailers.soriana import SorianaBlocked, SorianaScraper


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "soriana_chedraui_availability_test.xlsx"

SORIANA_BLOCKED_CATEGORIES = [
    "limpiadores",
    "afeitado-depilacion-dama",
    "desodorantes-para-caballero",
    "desodorantes-para-dama",
]

CHEDRAUI_CATEGORIES = [
    "higiene-bucal",
    "lavanderia",
]


def normalize_frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for column in COLUMNS:
        if column not in df.columns:
            df[column] = None
    return df[COLUMNS].copy()


def availability_summary(
    retailer: str,
    category_id: str,
    status: str,
    rows: list[dict],
    *,
    attempt: str,
    error: str | None = None,
) -> dict:
    df = normalize_frame(rows)

    if df.empty:
        availability = pd.Series(dtype=str)
    else:
        availability = (
            df["availability_status"]
            .fillna("UNKNOWN")
            .astype(str)
            .str.strip()
            .str.upper()
            .replace("", "UNKNOWN")
        )

    available = int(availability.eq("AVAILABLE").sum())
    unavailable = int(availability.eq("UNAVAILABLE").sum())
    unknown = int(availability.eq("UNKNOWN").sum())

    if df.empty:
        price_required_count = 0
        price_required_complete = 0
    else:
        price_required = ~availability.eq("UNAVAILABLE")
        price_required_count = int(price_required.sum())
        price_required_complete = int(
            df.loc[price_required, "price_current"].notna().sum()
        )

    return {
        "retailer": retailer,
        "category_id": category_id,
        "attempt": attempt,
        "status": status,
        "products": len(df),
        "available_products": available,
        "unavailable_products": unavailable,
        "availability_unknown": unknown,
        "availability_detected_pct": (
            round((available + unavailable) / len(df) * 100, 2)
            if len(df)
            else 0.0
        ),
        "price_required_products": price_required_count,
        "price_required_complete": price_required_complete,
        "sku_complete": (
            int(df["sku"].fillna("").astype(str).str.strip().ne("").sum())
            if not df.empty else 0
        ),
        "url_complete": (
            int(df["url"].fillna("").astype(str).str.strip().ne("").sum())
            if not df.empty else 0
        ),
        "error": error,
    }


def run_soriana(category, location, attempt: str) -> tuple[list[dict], dict]:
    scraper = SorianaScraper(
        headless=False,
        browser_channel="chrome",
        diagnostics_dir=ROOT / "diagnostics" / "soriana_availability_test",
    )
    rows: list[dict] = []
    status = "SUCCESS"
    error = None

    try:
        rows = scraper.scrape_category(category, location)
        if not rows:
            status = "EMPTY"
    except SorianaBlocked as exc:
        status = "BLOCKED"
        error = str(exc)
    except Exception as exc:
        status = "ERROR"
        error = f"{type(exc).__name__}: {exc}"

    return rows, availability_summary(
        "Soriana", category.id, status, rows, attempt=attempt, error=error
    )


def run_chedraui(category, location) -> tuple[list[dict], dict]:
    scraper = ChedrauiScraper(
        headless=False,
        browser_channel="chrome",
        diagnostics_dir=ROOT / "diagnostics" / "chedraui_availability_test",
    )
    rows: list[dict] = []
    status = "SUCCESS"
    error = None

    try:
        rows = scraper.scrape_category(category, location)
        if not rows:
            status = "EMPTY"
    except ChedrauiBlocked as exc:
        status = "BLOCKED"
        error = str(exc)
    except ChedrauiStoreContextError as exc:
        status = "STORE_CONTEXT_ERROR"
        error = str(exc)
    except Exception as exc:
        status = "ERROR"
        error = f"{type(exc).__name__}: {exc}"

    return rows, availability_summary(
        "Chedraui", category.id, status, rows, attempt="initial", error=error
    )


def print_summary(item: dict) -> None:
    print(
        "  -> "
        f"{item['status']} | products={item['products']} | "
        f"AVAILABLE={item['available_products']} | "
        f"UNAVAILABLE={item['unavailable_products']} | "
        f"UNKNOWN={item['availability_unknown']} | "
        f"detected={item['availability_detected_pct']}%",
        flush=True,
    )
    if item.get("error"):
        print(f"     {item['error']}", flush=True)


def write_output(frames: list[pd.DataFrame], summaries: list[dict]) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    concentrated = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=COLUMNS)
    )

    if not concentrated.empty:
        concentrated = concentrated.drop_duplicates(
            subset=["retailer", "category_id", "sku", "url", "product"],
            keep="last",
        ).reset_index(drop=True)

    status_text = (
        concentrated["availability_status"]
        .fillna("UNKNOWN")
        .astype(str)
        .str.strip()
        .str.upper()
        if not concentrated.empty
        else pd.Series(dtype=str)
    )

    unavailable = (
        concentrated.loc[status_text.eq("UNAVAILABLE")].copy()
        if not concentrated.empty
        else pd.DataFrame(columns=COLUMNS)
    )
    unknown = (
        concentrated.loc[status_text.isin(["", "UNKNOWN"])].copy()
        if not concentrated.empty
        else pd.DataFrame(columns=COLUMNS)
    )

    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        concentrated.to_excel(writer, index=False, sheet_name="Concentrado")
        pd.DataFrame(summaries).to_excel(
            writer, index=False, sheet_name="Resumen"
        )
        unavailable.to_excel(writer, index=False, sheet_name="NoDisponibles")
        unknown.to_excel(
            writer, index=False, sheet_name="DisponibilidadUnknown"
        )

        for sheet_name in (
            "Concentrado",
            "Resumen",
            "NoDisponibles",
            "DisponibilidadUnknown",
        ):
            ws = writer.book[sheet_name]
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for cell in ws[1]:
                cell.font = Font(bold=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Soriana bloqueados + disponibilidad Soriana/Chedraui"
    )
    parser.add_argument("--soriana-delay-seconds", type=int, default=60)
    parser.add_argument("--retry-delay-seconds", type=int, default=120)
    args = parser.parse_args()

    soriana_categories = {
        item.id: item
        for item in load_categories(
            ROOT / "config" / "soriana" / "categories.yaml"
        )
    }
    chedraui_categories = {
        item.id: item
        for item in load_categories(
            ROOT / "config" / "chedraui" / "categories.yaml"
        )
    }
    locations = {
        item.id: item
        for item in load_locations(ROOT / "config" / "locations.yaml")
    }

    soriana_location = locations["cdmx"]
    chedraui_location = locations["chedraui-polanco"]

    print("=" * 76)
    print("SORIANA BLOQUEADOS + DISPONIBILIDAD SORIANA / CHEDRAUI")
    print("=" * 76)
    print(f"Soriana delay : {args.soriana_delay_seconds}s")
    print(f"Retry delay   : {args.retry_delay_seconds}s")
    print(f"Output        : {OUTPUT}")
    print("")

    final_frames: dict[tuple[str, str], pd.DataFrame] = {}
    final_summaries: dict[tuple[str, str], dict] = {}
    blocked: list[str] = []

    print("SORIANA - PRIMER INTENTO")
    print("-" * 76)

    for index, category_id in enumerate(SORIANA_BLOCKED_CATEGORIES, start=1):
        print(
            f"[Soriana {index}/{len(SORIANA_BLOCKED_CATEGORIES)}] "
            f"{category_id}",
            flush=True,
        )
        rows, summary = run_soriana(
            soriana_categories[category_id],
            soriana_location,
            "initial",
        )
        print_summary(summary)

        key = ("Soriana", category_id)
        final_frames[key] = normalize_frame(rows)
        final_summaries[key] = summary

        if summary["status"] == "BLOCKED":
            blocked.append(category_id)

        if (
            index < len(SORIANA_BLOCKED_CATEGORIES)
            and args.soriana_delay_seconds > 0
        ):
            print(
                f"     pausa {args.soriana_delay_seconds}s...",
                flush=True,
            )
            time.sleep(args.soriana_delay_seconds)

    print("")
    print("CHEDRAUI - DISPONIBILIDAD")
    print("-" * 76)

    for index, category_id in enumerate(CHEDRAUI_CATEGORIES, start=1):
        print(
            f"[Chedraui {index}/{len(CHEDRAUI_CATEGORIES)}] {category_id}",
            flush=True,
        )
        rows, summary = run_chedraui(
            chedraui_categories[category_id],
            chedraui_location,
        )
        print_summary(summary)

        key = ("Chedraui", category_id)
        final_frames[key] = normalize_frame(rows)
        final_summaries[key] = summary

    if blocked:
        print("")
        print("SORIANA - REINTENTO FINAL")
        print("-" * 76)

        if args.retry_delay_seconds > 0:
            print(
                f"Pausa previa de {args.retry_delay_seconds}s...",
                flush=True,
            )
            time.sleep(args.retry_delay_seconds)

        for index, category_id in enumerate(blocked, start=1):
            print(
                f"[Retry {index}/{len(blocked)}] {category_id}",
                flush=True,
            )
            rows, summary = run_soriana(
                soriana_categories[category_id],
                soriana_location,
                "retry",
            )
            print_summary(summary)

            key = ("Soriana", category_id)
            final_summaries[key] = summary
            if summary["status"] == "SUCCESS":
                final_frames[key] = normalize_frame(rows)

            if (
                index < len(blocked)
                and args.soriana_delay_seconds > 0
            ):
                print(
                    f"     pausa {args.soriana_delay_seconds}s...",
                    flush=True,
                )
                time.sleep(args.soriana_delay_seconds)

    frames = [
        frame for frame in final_frames.values()
        if not frame.empty
    ]
    summaries = list(final_summaries.values())
    write_output(frames, summaries)

    print("")
    print("=" * 76)
    print("RESUMEN FINAL")
    print("=" * 76)

    summary_df = pd.DataFrame(summaries)
    print(
        summary_df[
            [
                "retailer",
                "category_id",
                "attempt",
                "status",
                "products",
                "available_products",
                "unavailable_products",
                "availability_unknown",
                "availability_detected_pct",
                "price_required_complete",
                "price_required_products",
            ]
        ].to_string(index=False)
    )

    print("")
    print(f"Output: {OUTPUT}")
    print(
        "Hojas: Concentrado, Resumen, NoDisponibles, DisponibilidadUnknown"
    )

    bad = summary_df["status"].isin(
        ["BLOCKED", "ERROR", "STORE_CONTEXT_ERROR"]
    )
    return 2 if bool(bad.any()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
