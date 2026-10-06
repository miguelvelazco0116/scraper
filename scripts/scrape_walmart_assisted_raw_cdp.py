from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import websocket
from openpyxl.styles import Font

from main import COLUMNS
from scraper.availability import availability_fields
from scraper.config import load_categories, load_locations
from scraper.parsers import clean_text, extract_sku, parse_money
from scraper.retailers.walmart import WalmartScraper

OUTPUT = ROOT / "output" / "walmart_assisted_raw_cdp.xlsx"
DIAG = ROOT / "diagnostics" / "walmart_assisted_raw_cdp"


class RawCDP:
    def __init__(self, ws_url: str, timeout: float = 20.0) -> None:
        self.ws = websocket.create_connection(
            ws_url,
            timeout=timeout,
            suppress_origin=True,
        )
        self.ws.settimeout(timeout)
        self._next_id = 0

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass

    def send(
        self,
        method: str,
        params: dict | None = None,
        *,
        session_id: str | None = None,
    ) -> dict:
        self._next_id += 1
        request_id = self._next_id
        payload = {"id": request_id, "method": method}
        if params:
            payload["params"] = params
        if session_id:
            payload["sessionId"] = session_id

        self.ws.send(json.dumps(payload))
        while True:
            raw = self.ws.recv()
            if not raw:
                continue
            message = json.loads(raw)
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(f"CDP {method} error: {message['error']}")
            return message.get("result") or {}

    def evaluate(self, session_id: str, expression: str):
        result = self.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
            session_id=session_id,
        )
        if result.get("exceptionDetails"):
            raise RuntimeError(
                f"Runtime.evaluate failed: {result['exceptionDetails']}"
            )
        return (result.get("result") or {}).get("value")


def find_walmart_target(cdp: RawCDP) -> dict | None:
    targets = cdp.send("Target.getTargets").get("targetInfos") or []
    candidates = [
        item
        for item in targets
        if item.get("type") == "page"
        and "walmart.com.mx" in (item.get("url") or "").casefold()
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            0
            if (item.get("url") or "").rstrip("/")
            == "https://www.walmart.com.mx"
            else 1,
            item.get("url") or "",
        )
    )
    return candidates[0]


def current_url(cdp: RawCDP, session_id: str) -> str:
    return str(cdp.evaluate(session_id, "location.href") or "")


def body_text(cdp: RawCDP, session_id: str) -> str:
    return str(
        cdp.evaluate(
            session_id,
            "document.body ? document.body.innerText : ''",
        )
        or ""
    )


def wait_ready(
    cdp: RawCDP,
    session_id: str,
    timeout_seconds: float = 45.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = cdp.evaluate(session_id, "document.readyState")
        if state in {"interactive", "complete"}:
            time.sleep(1.0)
            return
        time.sleep(0.5)
    raise TimeoutError("Walmart no terminó de cargar.")


def is_verification(cdp: RawCDP, session_id: str) -> bool:
    text = body_text(cdp, session_id).casefold()
    url = current_url(cdp, session_id).casefold()
    markers = (
        "access denied",
        "robot or human",
        "captcha",
        "reference #18",
        "forbidden",
        "verifica tu identidad",
        "mantén presionado",
        "manten presionado",
        "confirma que no eres un robot",
    )
    return "/blocked" in url or any(marker in text for marker in markers)


def wait_manual_verification(
    cdp: RawCDP,
    session_id: str,
    timeout_seconds: float = 300.0,
) -> None:
    if not is_verification(cdp, session_id):
        return

    print("")
    print("=" * 78)
    print("VERIFICACION MANUAL REQUERIDA")
    print("=" * 78)
    print(
        "Walmart mostró una verificación. Resuélvela manualmente "
        "en la pestaña de Chrome."
    )
    print(
        "El scraper permanecerá conectado y continuará automáticamente "
        "cuando desaparezca."
    )
    print("")

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        time.sleep(1.0)
        if not is_verification(cdp, session_id):
            print("VERIFICACION RESUELTA: continuando.")
            print("")
            time.sleep(1.0)
            return

    raise RuntimeError(
        "La verificación manual de Walmart no se resolvió dentro de "
        f"{int(timeout_seconds)} segundos."
    )


def is_high_traffic(cdp: RawCDP, session_id: str) -> bool:
    text = body_text(cdp, session_id).casefold()
    markers = (
        "we are experiencing high traffic",
        "experiencing high traffic",
        "please check after sometime",
        "please check after some time",
        "demasiado tráfico",
        "alto tráfico",
    )
    return any(marker in text for marker in markers)


def navigate(
    cdp: RawCDP,
    session_id: str,
    url: str,
    *,
    high_traffic_cooldown_seconds: float = 90.0,
    max_high_traffic_retries: int = 2,
) -> int:
    for attempt in range(max_high_traffic_retries + 1):
        cdp.send("Page.navigate", {"url": url}, session_id=session_id)
        wait_ready(cdp, session_id)
        time.sleep(1.2)
        wait_manual_verification(cdp, session_id)

        if not is_high_traffic(cdp, session_id):
            return attempt

        if attempt >= max_high_traffic_retries:
            raise RuntimeError("HIGH_TRAFFIC_PERSISTENT")

        print(
            "HIGH_TRAFFIC: Walmart pidió reducir el ritmo. "
            f"Cooldown de {int(high_traffic_cooldown_seconds)} s; "
            "después se reintentará la misma página."
        )
        time.sleep(high_traffic_cooldown_seconds)

    return max_high_traffic_retries


def store_context(cdp: RawCDP, session_id: str, location) -> dict:
    payload = cdp.evaluate(
        session_id,
        r"""
        (() => {
          let local = {};
          let session = {};
          try {
            local = Object.fromEntries(Object.entries(localStorage));
          } catch {}
          try {
            session = Object.fromEntries(Object.entries(sessionStorage));
          } catch {}
          return {
            body: document.body ? document.body.innerText : '',
            localStorage: local,
            sessionStorage: session
          };
        })()
        """,
    ) or {}

    blob = json.dumps(payload, ensure_ascii=False).casefold()
    store = str(location.store or "").casefold()
    postal = str(location.postal_code or "").casefold()
    store_id = str(location.store_id or "").casefold()

    hits = {
        "store_name": bool(store and store in blob),
        "postal_code": bool(postal and postal in blob),
        "store_id": bool(store_id and store_id in blob),
    }
    verified = sum(bool(value) for value in hits.values()) >= 2

    return {"verified": verified, "hits": hits}


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


def next_data_meta(cdp: RawCDP, session_id: str) -> dict:
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
            if isinstance(value.get("itemStacks"), list):
                candidates.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(data)
    if not candidates:
        return {}

    def score(candidate: dict) -> tuple[int, int]:
        total = candidate.get("aggregatedCount")
        try:
            total_int = int(total)
        except Exception:
            total_int = 0
        item_count = 0
        for stack in candidate.get("itemStacks") or []:
            if isinstance(stack, dict):
                item_count += len(stack.get("items") or [])
        return total_int, item_count

    result = max(candidates, key=score)

    item_ids: list[str] = []
    for stack in result.get("itemStacks") or []:
        if not isinstance(stack, dict):
            continue
        for item in stack.get("items") or []:
            if not isinstance(item, dict):
                continue
            raw_id = (
                item.get("usItemId")
                or item.get("itemId")
                or item.get("id")
            )
            if raw_id is None:
                continue
            value = str(raw_id).strip()
            if value and value not in item_ids:
                item_ids.append(value)

    total = (
        result.get("aggregatedCount")
        or result.get("totalCount")
        or result.get("count")
    )
    try:
        total = int(total)
    except Exception:
        total = None

    pagination = result.get("paginationV2") or result.get("pagination") or {}
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
        "published_total": total,
        "max_page": max_page,
        "item_ids": item_ids,
        "item_count": len(item_ids),
    }


def catalog_cards(
    cdp: RawCDP,
    session_id: str,
    allowed_item_ids: list[str] | None,
) -> dict:
    allowed_json = json.dumps(allowed_item_ids or [])
    return (
        cdp.evaluate(
            session_id,
            rf"""
            (() => {{
              const allowedIds = new Set({allowed_json}.map(String));
              const normalize = value =>
                String(value || '').replace(/s+/g, ' ').trim();

              const canonical = href => {{
                try {{
                  const u = new URL(href, location.href);
                  return u.origin + u.pathname;
                }} catch {{
                  return href || '';
                }}
              }};

              const money = /$s*[0-9][0-9,]*(?:.d{{1,2}})?/;
              const unavailable =
                /Agotado|No disponible|Sin existencia|Sin stock|Out of stock/i;

              const links = Array.from(
                document.querySelectorAll('a[href*="/ip/"]')
              ).filter(a => {{
                if (a.closest('header, nav, footer')) return false;

                if (allowedIds.size) {{
                  const dataCard = a.closest('[data-item-id]');
                  const cardId = dataCard
                    ? String(dataCard.getAttribute('data-item-id') || '')
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

              for (const a of links) {{
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

                    const productLinks = new Set(
                      Array.from(node.querySelectorAll('a[href*="/ip/"]'))
                        .map(link => canonical(link.href || ''))
                        .filter(Boolean)
                    );

                    if (
                      productLinks.size <= 2
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

                const text = normalize(
                  card.innerText || card.textContent || ''
                );
                if (!money.test(text) && !unavailable.test(text)) continue;

                const titleNode = card.querySelector(
                  '[data-automation-id="product-title"], ' +
                  '[data-automation-id*="product-title" i], ' +
                  '[data-testid*="product-title" i], h2, h3, h4'
                );
                const image = card.querySelector('img[alt]');

                const product =
                  normalize(
                    a.getAttribute('aria-label')
                    || a.getAttribute('title')
                    || a.innerText
                  )
                  || normalize(titleNode ? titleNode.innerText : '')
                  || normalize(image ? image.alt : '');

                if (!product) continue;

                seen.add(href);
                out.push({{href, product, text}});
              }}

              return {{
                allIpLinks: document.querySelectorAll(
                  'a[href*="/ip/"]'
                ).length,
                scopedIpLinks: links.length,
                cards: out
              }};
            }})()
            """,
        )
        or {}
    )


def cards_to_rows(
    cards: list[dict],
    parser: WalmartScraper,
    category,
    location,
) -> tuple[list[dict], dict]:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    rows: list[dict] = []
    stats = {
        "raw_cards": len(cards),
        "missing_identity": 0,
        "missing_price": 0,
    }

    for item in cards:
        url = clean_text(item.get("href"))
        product = clean_text(item.get("product"))
        text = clean_text(item.get("text")) or ""

        sku = extract_sku(url)
        if not sku or not product:
            stats["missing_identity"] += 1
            continue

        current_match = re.search(
            r"precios+actuals*(?:MXN)?s*$?s*([d,]+(?:.d{1,2})?)",
            text,
            re.I,
        )
        before_match = re.search(
            r"(?:Antes|costaba)s*$?s*([d,]+(?:.d{1,2})?)",
            text,
            re.I,
        )

        current = (
            parse_money(current_match.group(1))
            if current_match
            else None
        )
        regular = (
            parse_money(before_match.group(1))
            if before_match
            else None
        )

        if current is None:
            values = [
                parse_money(token)
                for token in re.findall(
                    r"$s*[d,]+(?:.d{1,2})?",
                    text,
                )
            ]
            values = [value for value in values if value is not None]
            current = values[0] if values else None
            if regular is None:
                regular = values[1] if len(values) > 1 else current

        if current is None:
            stats["missing_price"] += 1
            continue
        if regular is None:
            regular = current

        promos = []
        for pattern in (
            r"Rebaja",
            r"Precio en línea",
            r"Más vendido",
            r"Combinas+d+s*xs*$[d,.]+",
            r"Ahorras*$[d,.]+",
        ):
            match = re.search(pattern, text, re.I)
            if match:
                promos.append(match.group(0))

        availability = availability_fields(text=text)

        rows.append(
            {
                "scrape_timestamp": now,
                "retailer": "Walmart",
                "city": location.city,
                "state": location.state,
                "postal_code": location.postal_code,
                "store": location.store,
                "store_id": location.store_id,
                "department": category.department,
                "category": category.name,
                "subcategory": category.subcategory,
                "sub_subcategory": category.sub_subcategory,
                "category_id": category.id,
                "sku": sku,
                "brand": parser._infer_brand(product),
                "product": product,
                "price_current": current,
                "price_regular": regular,
                "promotion": clean_text(" | ".join(dict.fromkeys(promos))),
                **availability,
                "pickup_available": None,
                "store_context_verified": True,
                "store_context_method": "manual_session_cdp_sc_toreo",
                "url": url,
                "price_raw": text,
            }
        )

    stats["rows"] = len(rows)
    return rows, stats


def scrape_category(
    cdp: RawCDP,
    session_id: str,
    category,
    location,
    *,
    max_pages: int = 60,
    page_delay_seconds: float = 10.0,
    batch_size: int = 5,
    batch_cooldown_seconds: float = 45.0,
) -> tuple[pd.DataFrame, dict]:
    parser = WalmartScraper(
        headless=False,
        require_store_context=False,
        store_only=False,
    )

    unique: dict[str, dict] = {}
    pages: list[dict] = []
    published_total = None
    published_max_page = None
    no_new_pages = 0
    stopped_reason = None
    high_traffic_events = 0

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

        url = paged_url(category.url, page_number)

        try:
            retries = navigate(cdp, session_id, url)
            if retries:
                high_traffic_events += 1
        except RuntimeError as exc:
            if "HIGH_TRAFFIC_PERSISTENT" in str(exc):
                stopped_reason = "HIGH_TRAFFIC"
                print(
                    "  STOP_HIGH_TRAFFIC: se conserva la muestra "
                    f"hasta page={page_number - 1}."
                )
                break
            raise

        context = store_context(cdp, session_id, location)
        if not context["verified"]:
            raise RuntimeError(
                "STORE_CONTEXT_ERROR: no se pudo verificar SC Toreo / "
                f"{location.postal_code}. hits={context['hits']}"
            )

        next_meta = next_data_meta(cdp, session_id)

        if page_number == 1:
            published_total = next_meta.get("published_total")
            published_max_page = next_meta.get("max_page")

            if published_max_page is None and published_total:
                item_count = int(next_meta.get("item_count") or 0)
                if item_count > 0:
                    published_max_page = max(
                        1,
                        (published_total + item_count - 1) // item_count,
                    )

            print(
                "  CATALOG_META: "
                f"published_total={published_total} | "
                f"max_page={published_max_page} | "
                f"next_items={next_meta.get('item_count')}"
            )

        payload = catalog_cards(
            cdp,
            session_id,
            next_meta.get("item_ids") or None,
        )
        rows, stats = cards_to_rows(
            payload.get("cards") or [],
            parser,
            category,
            location,
        )

        new_count = 0
        for row in rows:
            sku = str(row.get("sku") or "").strip()
            if sku and sku not in unique:
                unique[sku] = row
                new_count += 1

        pages.append(
            {
                "page": page_number,
                "url": current_url(cdp, session_id),
                "all_ip": payload.get("allIpLinks"),
                "scoped_ip": payload.get("scopedIpLinks"),
                "raw_cards": stats["raw_cards"],
                "rows": stats["rows"],
                "new_rows": new_count,
                "cumulative": len(unique),
                "next_item_count": next_meta.get("item_count"),
            }
        )

        print(
            f"  page={page_number} | "
            f"all_ip={payload.get('allIpLinks')} | "
            f"scoped_ip={payload.get('scopedIpLinks')} | "
            f"cards={stats['raw_cards']} | rows={stats['rows']} | "
            f"new={new_count} | cumulative={len(unique)}"
        )

        if published_total is not None and len(unique) >= published_total:
            break

        no_new_pages = no_new_pages + 1 if new_count == 0 else 0
        if no_new_pages >= 2:
            break

        if (
            page_number >= 3
            and published_total is None
            and published_max_page is None
        ):
            stopped_reason = "NO_PAGINATION_METADATA"
            print(
                "  STOP_NO_METADATA: no se pudo validar total/maxPage; "
                "se conserva la muestra obtenida."
            )
            break

    frame = pd.DataFrame(list(unique.values()))
    for column in COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    frame = frame[COLUMNS].copy()

    status = (
        "PARTIAL"
        if len(frame) > 0 and stopped_reason
        else "SUCCESS"
        if len(frame) > 0
        else "EMPTY"
    )

    meta = {
        "retailer": "Walmart",
        "category_id": category.id,
        "status": status,
        "products": len(frame),
        "sku_complete": int(
            frame["sku"].fillna("").astype(str).str.strip().ne("").sum()
        ) if not frame.empty else 0,
        "price_complete": int(frame["price_current"].notna().sum())
        if not frame.empty else 0,
        "url_complete": int(
            frame["url"].fillna("").astype(str).str.strip().ne("").sum()
        ) if not frame.empty else 0,
        "published_total": published_total,
        "published_max_page": published_max_page,
        "pages_scanned": len(pages),
        "high_traffic_events": high_traffic_events,
        "stopped_reason": stopped_reason,
        "store_context_verified": True,
        "store": location.store,
        "postal_code": location.postal_code,
        "pages": pages,
    }
    return frame, meta


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Walmart México asistido sobre Chrome existente vía CDP."
    )
    parser.add_argument("--category", default="cuidado-bucal")
    parser.add_argument("--ws-url", required=True)
    parser.add_argument("--output", default=str(OUTPUT))
    args = parser.parse_args()

    categories = {
        item.id: item
        for item in load_categories(
            ROOT / "config" / "walmart" / "categories.yaml"
        )
    }
    category = categories.get(args.category)
    if category is None:
        raise SystemExit(f"Categoría Walmart no encontrada: {args.category}")

    location = next(
        item
        for item in load_locations(ROOT / "config" / "locations.yaml")
        if item.id == "sc-toreo"
    )

    print("=" * 82)
    print("WALMART MEXICO - SESION ASISTIDA CDP")
    print("=" * 82)
    print(f"Categoría : {category.id}")
    print(f"Tienda    : {location.store} | CP {location.postal_code}")
    print(f"Destino   : {category.url}")
    print("")

    cdp = RawCDP(args.ws_url)
    try:
        target = find_walmart_target(cdp)
        if target is None:
            print("ERROR: no se encontró una pestaña abierta de Walmart México.")
            return 1

        print(f"Pestaña detectada: {target.get('url')}")

        attached = cdp.send(
            "Target.attachToTarget",
            {"targetId": target["targetId"], "flatten": True},
        )
        session_id = attached["sessionId"]
        cdp.send("Runtime.enable", session_id=session_id)
        cdp.send("Page.enable", session_id=session_id)

        wait_manual_verification(cdp, session_id)

        initial_context = store_context(cdp, session_id, location)
        if not initial_context["verified"]:
            print("")
            print("STORE_CONTEXT_ERROR")
            print(
                "Antes de ejecutar, selecciona manualmente SC Toreo "
                f"(CP {location.postal_code}) en Walmart."
            )
            print(f"Señales detectadas: {initial_context['hits']}")
            return 4

        print(
            "Contexto tienda : VERIFICADO | "
            f"{initial_context['hits']}"
        )

        frame, meta = scrape_category(
            cdp,
            session_id,
            category,
            location,
        )

        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = ROOT / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        DIAG.mkdir(parents=True, exist_ok=True)

        (DIAG / f"{category.id}_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        summary = pd.DataFrame([meta | {"pages": None}])

        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            frame.to_excel(writer, index=False, sheet_name="Concentrado")
            summary.to_excel(writer, index=False, sheet_name="Resumen")
            for sheet_name in ("Concentrado", "Resumen"):
                ws = writer.book[sheet_name]
                ws.freeze_panes = "A2"
                ws.auto_filter.ref = ws.dimensions
                for cell in ws[1]:
                    cell.font = Font(bold=True)

        print("")
        print("RESULTADO")
        print("-" * 82)
        print(f"Status              : {meta['status']}")
        print(f"Productos           : {meta['products']}")
        print(f"SKU completos       : {meta['sku_complete']}")
        print(f"Precios completos   : {meta['price_complete']}")
        print(f"URLs completas      : {meta['url_complete']}")
        print(f"Total publicado     : {meta['published_total']}")
        print(f"Max page            : {meta['published_max_page']}")
        print(f"Páginas recorridas  : {meta['pages_scanned']}")
        print(f"High traffic events : {meta['high_traffic_events']}")
        print(f"Stop reason         : {meta['stopped_reason']}")
        print(f"Output              : {output_path}")

        return 0 if meta["status"] == "SUCCESS" else 2
    finally:
        cdp.close()


if __name__ == "__main__":
    raise SystemExit(main())
