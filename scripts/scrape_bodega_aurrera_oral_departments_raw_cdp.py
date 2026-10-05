from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
from openpyxl.styles import Font

from main import COLUMNS
from scraper.config import load_locations
from scraper.retailers.bodega_aurrera import BodegaAurreraScraper
from scrape_bodega_aurrera_raw_cdp import (
    DIAG,
    RawCDP,
    cards_to_rows,
    content_child_urls,
    current_url,
    expand_current_page,
    explicit_page_urls,
    find_bodega_target,
    navigate,
    product_link_count,
    raw_cards,
)

PARENT_URL = (
    "https://www.bodegaaurrera.com.mx/content/"
    "cuidado-personal/cuidado-bucal/264479_950014"
)

TARGETS = {
    "enjuagues-bucales": {
        "url": (
            "https://www.bodegaaurrera.com.mx/browse/"
            "cuidado-personal/cuidado-bucal/enjuagues-bucales/"
            "264479_950014_950025"
        ),
        "department": "Cuidado personal",
        "category": "Cuidado bucal",
        "subcategory": "Enjuagues bucales",
    },
    "pasta-dental": {
        "url": (
            "https://www.bodegaaurrera.com.mx/browse/"
            "cuidado-personal/cuidado-bucal/pasta-dental/"
            "264479_950014_950022"
        ),
        "department": "Cuidado personal",
        "category": "Cuidado bucal",
        "subcategory": "Pasta dental",
    },
}


OUTPUT = ROOT / "output" / "bodega_aurrera_enjuagues_pastas_raw_cdp.xlsx"
TEMP_DIR = ROOT / "output" / "_bodega_oral_departments"
ORAL_DIAG = ROOT / "diagnostics" / "bodega_aurrera_oral_departments"


def _norm(value: str | None) -> str:
    return " ".join(str(value or "").casefold().split())


def discover_target_urls(cdp: RawCDP, session_id: str) -> dict[str, str]:
    navigate(cdp, session_id, PARENT_URL)

    discovered = cdp.evaluate(
        session_id,
        r"""
        (() => {
          const norm = value => String(value || '')
            .replace(/s+/g, ' ')
            .trim()
            .toLowerCase();

          const rows = [];
          for (const a of Array.from(document.querySelectorAll('a[href]'))) {
            const href = a.href || '';
            const text = norm(
              a.innerText
              || a.textContent
              || a.getAttribute('aria-label')
              || a.getAttribute('title')
            );
            if (!href || !text) continue;
            if (
              text.includes('enjuagues bucales')
              || text.includes('enjuague bucal')
              || text.includes('pasta dental')
              || text.includes('pastas dentales')
            ) {
              rows.push({href, text});
            }
          }
          return rows;
        })()
        """,
    ) or []

    result: dict[str, str] = {}

    for family, spec in TARGETS.items():
        candidates = []
        for item in discovered:
            text = _norm(item.get("text"))
            href = str(item.get("href") or "")
            if not href:
                continue
            if any(label in text for label in spec["labels"]):
                candidates.append(href)

        # Prefer the actual browse taxonomy route if present.
        candidates = list(dict.fromkeys(candidates))
        candidates.sort(
            key=lambda url: (
                0 if "/browse/" in url else 1,
                len(url),
            )
        )
        if candidates:
            result[family] = candidates[0]

    # Fallback for UI buttons without hrefs: click the exact visible label,
    # capture the URL reached, then return to the parent page.
    for family, spec in TARGETS.items():
        if family in result:
            continue

        navigate(cdp, session_id, PARENT_URL)

        labels_json = json.dumps(spec["labels"], ensure_ascii=False)
        clicked = cdp.evaluate(
            session_id,
            f"""
            (() => {{
              const labels = {labels_json};
              const norm = value => String(value || '')
                .replace(/\s+/g, ' ')
                .trim()
                .toLowerCase();

              const visible = el => {{
                if (!el || !el.getBoundingClientRect) return false;
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== 'none'
                  && style.visibility !== 'hidden'
                  && rect.width > 3
                  && rect.height > 3;
              }};

              const nodes = Array.from(document.querySelectorAll(
                'button, a, [role="button"], div, span'
              ));

              for (const node of nodes) {{
                if (!visible(node)) continue;
                const text = norm(
                  node.innerText
                  || node.textContent
                  || node.getAttribute('aria-label')
                  || node.getAttribute('title')
                );
                if (!labels.some(label => text === label || text.includes(label))) {{
                  continue;
                }}

                let clickable = node.closest(
                  'a, button, [role="button"]'
                ) || node;

                clickable.scrollIntoView({{
                  block: 'center',
                  inline: 'center',
                  behavior: 'instant'
                }});
                clickable.click();

                return {{
                  clicked: true,
                  text,
                  tag: clickable.tagName
                }};
              }}

              return {{clicked: false}};
            }})()
            """,
        ) or {"clicked": False}

        if not clicked.get("clicked"):
            continue

        before = PARENT_URL.rstrip("/")
        deadline = time.monotonic() + 12
        reached = None
        while time.monotonic() < deadline:
            time.sleep(0.5)
            url = current_url(cdp, session_id)
            if url and url.rstrip("/") != before:
                reached = url
                break

        if reached:
            result[family] = reached

    return result


def build_category(family: str):
    spec = TARGETS[family]
    return SimpleNamespace(
        id=family,
        department=spec["department"],
        name=spec["category"],
        subcategory=spec["subcategory"],
        sub_subcategory=None,
    )


def scrape_route(
    cdp: RawCDP,
    session_id: str,
    family: str,
    route_url: str,
    scraper: BodegaAurreraScraper,
    location,
) -> tuple[pd.DataFrame, dict]:
    category = build_category(family)

    navigate(cdp, session_id, route_url)
    initial_links = product_link_count(cdp, session_id)
    expansion = expand_current_page(cdp, session_id)
    pages = explicit_page_urls(cdp, session_id)

    cards = raw_cards(cdp, session_id)
    rows, extraction = cards_to_rows(
        cards,
        scraper,
        category,
        location,
    )

    page_meta = []
    for page_url in pages:
        navigate(cdp, session_id, page_url)
        page_expansion = expand_current_page(cdp, session_id)
        page_cards = raw_cards(cdp, session_id)
        page_rows, page_stats = cards_to_rows(
            page_cards,
            scraper,
            category,
            location,
        )
        rows.extend(page_rows)
        page_meta.append(
            {
                "url": page_url,
                "product_links": page_expansion["product_links"],
                "stabilized": page_expansion["stabilized"],
                "raw_cards": page_stats["raw_cards"],
                "rows": page_stats["rows"],
            }
        )

    unique = {}
    for row in rows:
        sku = str(row.get("sku") or "").strip()
        if sku:
            unique[sku] = row

    frame = pd.DataFrame(list(unique.values()))
    for column in COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    frame = frame[COLUMNS].copy()

    meta = {
        "family": family,
        "route_url": route_url,
        "actual_url": current_url(cdp, session_id),
        "initial_product_links": initial_links,
        "product_links": expansion["product_links"],
        "products": len(frame),
        "sku_complete": int(
            frame["sku"].fillna("").astype(str).str.strip().ne("").sum()
        ) if not frame.empty else 0,
        "price_complete": int(
            frame["price_current"].notna().sum()
        ) if not frame.empty else 0,
        "url_complete": int(
            frame["url"].fillna("").astype(str).str.strip().ne("").sum()
        ) if not frame.empty else 0,
        "raw_cards": extraction["raw_cards"],
        "missing_sku": extraction["rejected_missing_sku"],
        "missing_price": extraction["rejected_missing_price"],
        "scroll_rounds": len(expansion["rounds"]),
        "stabilized": expansion["stabilized"],
        "explicit_pages": len(pages),
        "explicit_page_meta": page_meta,
        "status": (
            "SUCCESS"
            if len(frame) > 0
            else "EMPTY"
        ),
    }
    return frame, meta


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Muestra completa de Enjuagues Bucales y Pasta Dental "
            "de Bodega Aurrera usando Chrome existente y CDP directo."
        )
    )
    parser.add_argument("--ws-url", required=True)
    args = parser.parse_args()

    location = next(
        item
        for item in load_locations(ROOT / "config" / "locations.yaml")
        if item.id == "bodega-aurrera-online"
    )
    scraper = BodegaAurreraScraper(headless=False)

    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    ORAL_DIAG.mkdir(parents=True, exist_ok=True)

    print("=" * 84)
    print("BODEGA AURRERA - ENJUAGUES BUCALES + PASTA DENTAL")
    print("=" * 84)
    print("Ramas      : Cuidado personal > Cuidado bucal > Enjuagues/Pasta dental")
    print("")

    cdp = RawCDP(args.ws_url)
    try:
        target = find_bodega_target(cdp)
        if target is None:
            print("ERROR: no se encontró una pestaña abierta de Bodega Aurrera.")
            return 1

        print(f"Pestaña detectada: {target.get('url')}")

        attached = cdp.send(
            "Target.attachToTarget",
            {"targetId": target["targetId"], "flatten": True},
        )
        session_id = attached["sessionId"]
        cdp.send("Runtime.enable", session_id=session_id)
        cdp.send("Page.enable", session_id=session_id)

        urls = {
            family: spec["url"]
            for family, spec in TARGETS.items()
        }

        print("")
        print("RUTAS OBJETIVO")
        for family in TARGETS:
            print(f"  {family}: {urls[family]}")

        frames = []
        summaries = []

        for index, family in enumerate(TARGETS, start=1):
            print("")
            print("-" * 84)
            print(f"[{index}/{len(TARGETS)}] {family}")

            frame, meta = scrape_route(
                cdp,
                session_id,
                family,
                urls[family],
                scraper,
                location,
            )
            frames.append(frame)
            summaries.append(meta)

            category_file = TEMP_DIR / f"{family}.xlsx"
            with pd.ExcelWriter(category_file, engine="openpyxl") as writer:
                frame.to_excel(writer, index=False, sheet_name="Concentrado")
                pd.DataFrame([meta | {"explicit_page_meta": None}]).to_excel(
                    writer,
                    index=False,
                    sheet_name="Resumen",
                )

            (ORAL_DIAG / f"{family}_meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            print(
                f"Resultado: {meta['status']} | "
                f"links={meta['initial_product_links']}->{meta['product_links']} | "
                f"products={meta['products']} | "
                f"sku={meta['sku_complete']} | "
                f"price={meta['price_complete']} | "
                f"url={meta['url_complete']} | "
                f"raw_cards={meta['raw_cards']} | "
                f"pages={meta['explicit_pages']}"
            )

        concentrated = pd.concat(frames, ignore_index=True)
        summary_df = pd.DataFrame(
            [
                item | {"explicit_page_meta": None}
                for item in summaries
            ]
        )

        with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
            concentrated.to_excel(
                writer,
                index=False,
                sheet_name="Concentrado",
            )
            summary_df.to_excel(
                writer,
                index=False,
                sheet_name="Resumen",
            )
            for sheet_name in ("Concentrado", "Resumen"):
                ws = writer.book[sheet_name]
                ws.freeze_panes = "A2"
                ws.auto_filter.ref = ws.dimensions
                for cell in ws[1]:
                    cell.font = Font(bold=True)

        print("")
        print("=" * 84)
        print("RESUMEN FINAL")
        print("=" * 84)
        print(
            summary_df[
                [
                    "family",
                    "status",
                    "initial_product_links",
                    "product_links",
                    "products",
                    "sku_complete",
                    "price_complete",
                    "url_complete",
                    "explicit_pages",
                ]
            ].to_string(index=False)
        )
        print(f"Filas totales: {len(concentrated)}")
        print(f"Output      : {OUTPUT}")
        print("Consolidado : sin cambios")

        return 0
    finally:
        cdp.close()


if __name__ == "__main__":
    raise SystemExit(main())
