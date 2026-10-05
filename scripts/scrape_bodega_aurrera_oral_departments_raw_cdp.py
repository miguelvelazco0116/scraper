from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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


class BodegaHighTraffic(RuntimeError):
    pass


def is_high_traffic(cdp: RawCDP, session_id: str) -> bool:
    text = str(
        cdp.evaluate(
            session_id,
            "document.body ? document.body.innerText : ''",
        )
        or ""
    ).casefold()

    markers = (
        "we are experiencing high traffic",
        "experiencing high traffic",
        "please check after sometime",
        "please check after some time",
        "demasiado tráfico",
        "alto tráfico",
    )
    return any(marker in text for marker in markers)


def navigate_with_backoff(
    cdp: RawCDP,
    session_id: str,
    url: str,
    *,
    cooldown_seconds: float = 90.0,
    max_retries: int = 2,
) -> int:
    for attempt in range(max_retries + 1):
        navigate(cdp, session_id, url)

        if not is_high_traffic(cdp, session_id):
            return attempt

        if attempt >= max_retries:
            raise BodegaHighTraffic(
                "Bodega Aurrera mantiene el mensaje de high traffic "
                f"después de {max_retries + 1} intentos."
            )

        print(
            "HIGH_TRAFFIC: Bodega Aurrera pidió reducir el ritmo. "
            f"Cooldown de {int(cooldown_seconds)} s antes de reintentar "
            f"la misma página ({attempt + 1}/{max_retries})."
        )
        time.sleep(cooldown_seconds)

    return max_retries

def paged_url(url: str, page_number: int) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["page"] = str(page_number)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query),
            parts.fragment,
        )
    )


def next_data_catalog_meta(
    cdp: RawCDP,
    session_id: str,
) -> dict:
    raw = cdp.evaluate(
        session_id,
        """
        (() => {
          const node = document.getElementById('__NEXT_DATA__');
          return node ? node.textContent : null;
        })()
        """,
    )
    if not raw:
        return {}

    try:
        data = json.loads(raw)
    except Exception:
        return {}

    candidates: list[dict] = []

    def walk(value):
        if isinstance(value, dict):
            item_stacks = value.get("itemStacks")
            if isinstance(item_stacks, list):
                candidates.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(data)
    if not candidates:
        return {}

    def candidate_score(candidate: dict) -> tuple[int, int]:
        items = []
        for stack in candidate.get("itemStacks") or []:
            if isinstance(stack, dict):
                items.extend(stack.get("items") or [])
        count = candidate.get("aggregatedCount")
        try:
            count_int = int(count)
        except Exception:
            count_int = 0
        return (count_int, len(items))

    search_result = max(candidates, key=candidate_score)

    items: list[dict] = []
    for stack in search_result.get("itemStacks") or []:
        if not isinstance(stack, dict):
            continue
        for item in stack.get("items") or []:
            if isinstance(item, dict):
                items.append(item)

    item_ids: list[str] = []
    for item in items:
        raw_id = (
            item.get("usItemId")
            or item.get("itemId")
            or item.get("id")
        )
        if raw_id is None:
            continue
        item_id = str(raw_id).strip()
        if item_id and item_id not in item_ids:
            item_ids.append(item_id)

    total = (
        search_result.get("aggregatedCount")
        or search_result.get("totalCount")
        or search_result.get("count")
    )
    try:
        total = int(total)
    except Exception:
        total = None

    pagination = (
        search_result.get("paginationV2")
        or search_result.get("pagination")
        or {}
    )
    max_page = None
    if isinstance(pagination, dict):
        max_page = (
            pagination.get("maxPage")
            or pagination.get("maxPages")
            or pagination.get("totalPages")
        )
    try:
        max_page = int(max_page)
    except Exception:
        max_page = None

    return {
        "aggregated_count": total,
        "max_page": max_page,
        "item_ids": item_ids,
        "item_count": len(item_ids),
    }

def catalog_cards(
    cdp: RawCDP,
    session_id: str,
    family: str,
    allowed_item_ids: list[str] | None = None,
) -> dict:
    spec = TARGETS[family]
    expected_heading = spec["subcategory"].casefold()
    allowed_json = json.dumps(allowed_item_ids or [])

    payload = cdp.evaluate(
        session_id,
        rf"""
        (() => {{
          const expected = {json.dumps(expected_heading, ensure_ascii=False)};
          const allowedIds = new Set({allowed_json}.map(String));
          const normalize = value =>
            String(value || '').replace(/\s+/g, ' ').trim();

          const lower = value => normalize(value).toLowerCase();

          const headings = Array.from(
            document.querySelectorAll('h1, h2')
          );
          const heading = headings.find(h =>
            lower(h.innerText || h.textContent).includes(expected)
          ) || document.querySelector('h1');

          const isAfterHeading = el => {{
            if (!heading) return true;
            const pos = heading.compareDocumentPosition(el);
            return Boolean(pos & Node.DOCUMENT_POSITION_FOLLOWING);
          }};

          const canonical = href => {{
            try {{
              const u = new URL(href, location.href);
              return u.origin + u.pathname;
            }} catch {{
              return href || '';
            }}
          }};

          const money = /\$\s*[0-9][0-9,]*(?:\.\d{{1,2}})?/;
          const unavailable =
            /Agotado|No disponible|Sin existencia|Sin stock|Out of stock/i;

          const allLinks = Array.from(
            document.querySelectorAll('a[href*="/ip/"]')
          ).filter(a => {{
            if (a.closest('header, nav, footer')) return false;
            if (!isAfterHeading(a)) return false;

            if (allowedIds.size) {{
              const card = a.closest('[data-item-id]');
              const cardId = card
                ? String(card.getAttribute('data-item-id') || '')
                : '';
              const href = canonical(a.href || '');
              const hrefMatch = Array.from(allowedIds).some(id =>
                href.endsWith('/' + id)
              );
              if (!allowedIds.has(cardId) && !hrefMatch) return false;
            }}

            return true;
          }});

          const out = [];
          const seen = new Set();

          for (const a of allLinks) {{
            const href = canonical(a.href || '');
            if (!href || seen.has(href)) continue;

            let card =
              a.closest(
                '[data-item-id], [data-testid*="item" i], ' +
                '[data-automation-id*="product" i], article, li'
              );

            if (!card) {{
              let node = a;
              for (
                let i = 0;
                i < 14 && node;
                i++, node = node.parentElement
              ) {{
                const text = normalize(
                  node.innerText || node.textContent
                );
                if (!text) continue;

                const productHrefs = new Set(
                  Array.from(
                    node.querySelectorAll('a[href*="/ip/"]')
                  )
                    .map(link => canonical(link.href || ''))
                    .filter(Boolean)
                );

                if (
                  productHrefs.size <= 2
                  && (money.test(text) || unavailable.test(text))
                  && text.length >= 8
                  && text.length <= 6000
                ) {{
                  card = node;
                  break;
                }}
              }}
            }}

            if (!card) continue;

            const cardText = normalize(
              card.innerText || card.textContent || ''
            );
            if (
              !money.test(cardText)
              && !unavailable.test(cardText)
            ) {{
              continue;
            }}

            const titleNode =
              card.querySelector(
                '[data-automation-id="product-title"], ' +
                '[data-automation-id*="product-title" i], ' +
                '[data-testid*="product-title" i], h2, h3, h4'
              );

            const image = card.querySelector('img[alt]');
            const brandNode = card.querySelector(
              '[data-automation-id*="brand" i], [class*="brand" i]'
            );

            let title =
              normalize(
                a.getAttribute('aria-label')
                || a.getAttribute('title')
                || a.innerText
              )
              || normalize(titleNode ? titleNode.innerText : '')
              || normalize(image ? image.alt : '');

            if (!title) {{
              const lines = String(card.innerText || '')
                .split(/\n+/)
                .map(normalize)
                .filter(Boolean);
              title = lines.find(line =>
                line.length > 6
                && !money.test(line)
                && !/Agregar|Añadir|Entrega|Envío|Rebaja|Antes|mensualidades/i.test(line)
              ) || '';
            }}

            if (!title) continue;

            seen.add(href);
            out.push({{
              href,
              title,
              brand: normalize(
                brandNode
                  ? brandNode.innerText || brandNode.textContent
                  : ''
              ),
              text: cardText
            }});
          }}

          return {{
            heading: heading
              ? normalize(heading.innerText || heading.textContent)
              : null,
            totalIpLinks: document.querySelectorAll(
              'a[href*="/ip/"]'
            ).length,
            scopedIpLinks: allLinks.length,
            cards: out
          }};
        }})()
        """,
    ) or {}

    return payload


def wait_for_catalog(
    cdp: RawCDP,
    session_id: str,
    family: str,
    allowed_item_ids: list[str] | None = None,
    timeout_seconds: float = 20.0,
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last = {}
    while time.monotonic() < deadline:
        last = catalog_cards(
            cdp,
            session_id,
            family,
            allowed_item_ids=allowed_item_ids,
        )
        if (last.get("cards") or []):
            time.sleep(0.8)
            return last
        time.sleep(0.5)
    return last

def scrape_route(
    cdp: RawCDP,
    session_id: str,
    family: str,
    route_url: str,
    scraper: BodegaAurreraScraper,
    location,
    max_pages: int = 60,
    page_delay_seconds: float = 10.0,
    batch_size: int = 5,
    batch_cooldown_seconds: float = 45.0,
    high_traffic_cooldown_seconds: float = 90.0,
    high_traffic_retries: int = 2,
) -> tuple[pd.DataFrame, dict]:
    category = build_category(family)

    unique_rows: dict[str, dict] = {}
    page_meta: list[dict] = []
    empty_or_duplicate_pages = 0
    stopped_reason = None
    high_traffic_events = 0
    published_total = None
    published_max_page = None
    pagination_metadata_verified = False

    for page_number in range(1, max_pages + 1):
        if published_max_page is not None and page_number > published_max_page:
            break
        if page_number > 1:
            if batch_size > 0 and (page_number - 1) % batch_size == 0:
                print(
                    f"  BATCH_COOLDOWN: {int(batch_cooldown_seconds)} s "
                    f"después de {page_number - 1} páginas."
                )
                time.sleep(batch_cooldown_seconds)
            else:
                time.sleep(page_delay_seconds)

        url = paged_url(route_url, page_number)

        try:
            retries_used = navigate_with_backoff(
                cdp,
                session_id,
                url,
                cooldown_seconds=high_traffic_cooldown_seconds,
                max_retries=high_traffic_retries,
            )
            if retries_used:
                high_traffic_events += 1
        except BodegaHighTraffic as exc:
            stopped_reason = "HIGH_TRAFFIC"
            print(
                "  STOP_HIGH_TRAFFIC: se conserva la muestra acumulada "
                f"hasta page={page_number - 1}. {exc}"
            )
            break

        next_meta = next_data_catalog_meta(cdp, session_id)
        if page_number == 1:
            published_total = next_meta.get("aggregated_count")
            published_max_page = next_meta.get("max_page")
            pagination_metadata_verified = bool(
                published_total is not None
                or published_max_page is not None
            )

            print(
                "  CATALOG_META: "
                f"published_total={published_total} | "
                f"max_page={published_max_page} | "
                f"next_items={next_meta.get('item_count')}"
            )

            if published_max_page is None and published_total:
                first_page_items = int(next_meta.get("item_count") or 0)
                if first_page_items > 0:
                    published_max_page = max(
                        1,
                        (published_total + first_page_items - 1)
                        // first_page_items,
                    )
                    print(
                        "  CATALOG_META: max_page inferido="
                        f"{published_max_page}"
                    )

        payload = wait_for_catalog(
            cdp,
            session_id,
            family,
            allowed_item_ids=next_meta.get("item_ids") or None,
        )

        cards = payload.get("cards") or []
        rows, extraction = cards_to_rows(
            cards,
            scraper,
            category,
            location,
        )

        new_count = 0
        for row in rows:
            sku = str(row.get("sku") or "").strip()
            if not sku:
                continue
            if sku not in unique_rows:
                unique_rows[sku] = row
                new_count += 1

        page_info = {
            "page": page_number,
            "requested_url": url,
            "actual_url": current_url(cdp, session_id),
            "heading": payload.get("heading"),
            "total_ip_links": payload.get("totalIpLinks"),
            "scoped_ip_links": payload.get("scopedIpLinks"),
            "raw_cards": extraction["raw_cards"],
            "rows": extraction["rows"],
            "new_rows": new_count,
            "missing_sku": extraction["rejected_missing_sku"],
            "missing_price": extraction["rejected_missing_price"],
            "cumulative": len(unique_rows),
            "high_traffic_retries": retries_used,
            "next_data_item_count": next_meta.get("item_count"),
            "published_total": published_total,
            "published_max_page": published_max_page,
        }
        page_meta.append(page_info)

        print(
            f"  page={page_number} | "
            f"all_ip={page_info['total_ip_links']} | "
            f"scoped_ip={page_info['scoped_ip_links']} | "
            f"cards={page_info['raw_cards']} | "
            f"rows={page_info['rows']} | "
            f"new={new_count} | "
            f"cumulative={len(unique_rows)}"
        )

        if published_total is not None and len(unique_rows) >= published_total:
            break

        if extraction["rows"] == 0 or new_count == 0:
            empty_or_duplicate_pages += 1
        else:
            empty_or_duplicate_pages = 0

        # Two consecutive empty/duplicate pages means the catalog has ended
        # or the requested page has been clamped back to the last page.
        if empty_or_duplicate_pages >= 2:
            break

        if (
            page_number >= 3
            and not pagination_metadata_verified
            and published_max_page is None
        ):
            stopped_reason = "NO_PAGINATION_METADATA"
            print(
                "  STOP_NO_METADATA: no fue posible validar total/maxPage "
                "desde __NEXT_DATA__; se conserva la muestra obtenida."
            )
            break

    frame = pd.DataFrame(list(unique_rows.values()))
    for column in COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    frame = frame[COLUMNS].copy()

    meta = {
        "family": family,
        "route_url": route_url,
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
        "pages_scanned": len(page_meta),
        "page_meta": page_meta,
        "high_traffic_events": high_traffic_events,
        "published_total": published_total,
        "published_max_page": published_max_page,
        "pagination_metadata_verified": pagination_metadata_verified,
        "stopped_reason": stopped_reason,
        "status": (
            "PARTIAL"
            if len(frame) > 0 and stopped_reason
            else "SUCCESS"
            if (
                len(frame) > 0
                and (
                    published_total is None
                    or len(frame) >= published_total
                    or (
                        published_max_page is not None
                        and len(page_meta) >= published_max_page
                    )
                )
            )
            else "PARTIAL"
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
                pd.DataFrame([meta | {"page_meta": None}]).to_excel(
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
                f"products={meta['products']} | "
                f"sku={meta['sku_complete']} | "
                f"price={meta['price_complete']} | "
                f"url={meta['url_complete']} | "
                f"pages_scanned={meta['pages_scanned']} | "
                f"published_total={meta['published_total']} | "
                f"max_page={meta['published_max_page']} | "
                f"high_traffic={meta['high_traffic_events']} | "
                f"stopped={meta['stopped_reason']}"
            )

        concentrated = pd.concat(frames, ignore_index=True)
        summary_df = pd.DataFrame(
            [
                item | {"page_meta": None}
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
                    "products",
                    "sku_complete",
                    "price_complete",
                    "url_complete",
                    "pages_scanned",
                    "published_total",
                    "published_max_page",
                    "pagination_metadata_verified",
                    "high_traffic_events",
                    "stopped_reason",
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
