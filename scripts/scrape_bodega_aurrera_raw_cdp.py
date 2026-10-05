from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import websocket
from openpyxl.styles import Font

from main import COLUMNS
from scraper.availability import UNAVAILABLE, availability_fields
from scraper.config import load_categories, load_locations
from scraper.parsers import clean_text, extract_sku
from scraper.retailers.bodega_aurrera import BASE_URL, BodegaAurreraScraper

OUTPUT = ROOT / "output" / "bodega_aurrera_raw_cdp.xlsx"
DIAG = ROOT / "diagnostics" / "bodega_aurrera_raw_cdp"


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


def find_bodega_target(cdp: RawCDP) -> dict | None:
    targets = cdp.send("Target.getTargets").get("targetInfos") or []
    candidates = [
        item
        for item in targets
        if item.get("type") == "page"
        and "bodegaaurrera.com.mx" in (item.get("url") or "").casefold()
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            0 if "/browse/" not in (item.get("url") or "") else 1,
            item.get("url") or "",
        )
    )
    return candidates[0]


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
    raise TimeoutError("La página no terminó de cargar.")


def body_text(cdp: RawCDP, session_id: str) -> str:
    return str(
        cdp.evaluate(
            session_id,
            "document.body ? document.body.innerText : ''",
        )
        or ""
    )


def current_url(cdp: RawCDP, session_id: str) -> str:
    return str(cdp.evaluate(session_id, "location.href") or "")


def assert_not_blocked(cdp: RawCDP, session_id: str) -> None:
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
    if "/blocked" in url or any(marker in text for marker in markers):
        raise RuntimeError(
            "Bodega Aurrera mostró un bloqueo o verificación. "
            "No se intenta evadir la protección."
        )


def navigate(
    cdp: RawCDP,
    session_id: str,
    url: str,
) -> None:
    cdp.send("Page.navigate", {"url": url}, session_id=session_id)
    wait_ready(cdp, session_id)
    time.sleep(1.2)
    assert_not_blocked(cdp, session_id)


def product_link_count(cdp: RawCDP, session_id: str) -> int:
    return int(
        cdp.evaluate(
            session_id,
            """
            (() => new Set(
              Array.from(document.querySelectorAll('a[href*="/ip/"]'))
                .map(a => a.href || '')
                .filter(Boolean)
            ).size)()
            """,
        )
        or 0
    )


def click_load_more(cdp: RawCDP, session_id: str) -> bool:
    return bool(
        cdp.evaluate(
            session_id,
            r"""
            (() => {
              const norm = value => String(value || '')
                .replace(/s+/g, ' ')
                .trim()
                .toLowerCase();

              const labels = [
                'ver más',
                'ver mas',
                'mostrar más',
                'mostrar mas',
                'cargar más',
                'cargar mas'
              ];

              const visible = el => {
                if (!el || !el.getBoundingClientRect) return false;
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== 'none'
                  && style.visibility !== 'hidden'
                  && rect.width > 3
                  && rect.height > 3;
              };

              const nodes = Array.from(document.querySelectorAll(
                'button, a, [role="button"]'
              ));

              for (const node of nodes) {
                if (!visible(node)) continue;
                const text = norm(
                  node.innerText
                  || node.textContent
                  || node.getAttribute('aria-label')
                  || node.getAttribute('title')
                );
                if (!labels.some(label => text.includes(label))) continue;

                node.scrollIntoView({
                  block: 'center',
                  inline: 'center',
                  behavior: 'instant'
                });
                node.click();
                return true;
              }

              return false;
            })()
            """,
        )
    )


def expand_current_page(
    cdp: RawCDP,
    session_id: str,
    max_rounds: int = 30,
) -> dict:
    previous = product_link_count(cdp, session_id)
    stale_rounds = 0
    trace: list[dict] = []

    for round_number in range(1, max_rounds + 1):
        clicked = click_load_more(cdp, session_id)

        cdp.evaluate(
            session_id,
            "window.scrollTo(0, document.body.scrollHeight)",
        )
        time.sleep(1.2)

        current = product_link_count(cdp, session_id)
        trace.append(
            {
                "round": round_number,
                "before": previous,
                "after": current,
                "clicked_load_more": clicked,
            }
        )

        if current > previous:
            stale_rounds = 0
        else:
            stale_rounds += 1

        previous = max(previous, current)
        if stale_rounds >= 3:
            break

    return {
        "product_links": previous,
        "rounds": trace,
        "stabilized": stale_rounds >= 3,
    }


def explicit_page_urls(cdp: RawCDP, session_id: str) -> list[str]:
    hrefs = cdp.evaluate(
        session_id,
        """
        (() => Array.from(
          new Set(
            Array.from(document.querySelectorAll('a[href*="page="]'))
              .map(a => a.href || '')
              .filter(Boolean)
          )
        ))()
        """,
    ) or []

    valid = []
    for href in hrefs:
        try:
            query = dict(parse_qsl(urlsplit(href).query, keep_blank_values=True))
            page_no = int(query.get("page", "0"))
        except (TypeError, ValueError):
            continue
        if page_no > 1 and href not in valid:
            valid.append(href)

    return sorted(
        valid,
        key=lambda href: int(
            dict(parse_qsl(urlsplit(href).query)).get("page", "0")
        ),
    )


def content_child_urls(
    cdp: RawCDP,
    session_id: str,
    parent_url: str,
) -> list[str]:
    parent = urlsplit(parent_url)
    parent_path = parent.path.rstrip("/")
    if "/content/" not in parent_path:
        return []

    parts = [part for part in parent_path.split("/") if part]
    if len(parts) < 3:
        return []

    prefix = "/" + "/".join(parts[:-1]) + "/"
    hrefs = cdp.evaluate(
        session_id,
        """
        (() => Array.from(
          new Set(
            Array.from(document.querySelectorAll('a[href]'))
              .map(a => a.href || '')
              .filter(Boolean)
          )
        ))()
        """,
    ) or []

    children = []
    for href in hrefs:
        parts = urlsplit(href)
        if parts.netloc != parent.netloc:
            continue
        child_path = parts.path.rstrip("/")
        if not child_path.startswith(prefix):
            continue
        if child_path == parent_path:
            continue
        clean_url = f"{parts.scheme}://{parts.netloc}{parts.path}"
        if clean_url not in children:
            children.append(clean_url)

    return children


def raw_cards(cdp: RawCDP, session_id: str) -> list[dict]:
    return (
        cdp.evaluate(
            session_id,
            r"""
            (() => {
              const normalize = value =>
                String(value || '').replace(/s+/g, ' ').trim();

              const out = [];
              const seen = new Set();
              const unavailable =
                /Agotado|No disponible|Sin existencia|Sin stock|Out of stock/i;

              for (const a of Array.from(
                document.querySelectorAll('a[href*="/ip/"]')
              )) {
                const href = a.href || '';
                if (!href || seen.has(href)) continue;

                let node = a;
                let found = null;

                for (let i = 0; i < 12 && node; i++, node = node.parentElement) {
                  const text = normalize(node.innerText || node.textContent);
                  if (
                    text
                    && (
                      /$s*[0-9][0-9,]*(?:.d{1,2})?/.test(text)
                      || unavailable.test(text)
                    )
                    && text.length >= 10
                    && text.length <= 3500
                  ) {
                    const productLinks =
                      node.querySelectorAll('a[href*="/ip/"]').length;
                    if (productLinks <= 3) {
                      found = node;
                      break;
                    }
                  }
                }

                if (!found) continue;

                const text = (found.innerText || found.textContent || '').trim();
                const heading = found.querySelector(
                  'h1,h2,h3,h4,h5,[class*="name"],[class*="title"]'
                );
                const image = found.querySelector('img[alt]');
                const brandNode = found.querySelector(
                  '[data-automation-id*="brand"], [class*="brand"]'
                );

                const title =
                  normalize(a.getAttribute('aria-label'))
                  || normalize(a.getAttribute('title'))
                  || normalize(a.innerText)
                  || normalize(heading ? heading.innerText : '')
                  || normalize(image ? image.alt : '');

                const brand = normalize(
                  brandNode ? brandNode.innerText || brandNode.textContent : ''
                );

                seen.add(href);
                out.push({href, title, brand, text});
              }

              return out;
            })()
            """,
        )
        or []
    )


def cards_to_rows(
    cards: list[dict],
    scraper: BodegaAurreraScraper,
    category,
    location,
) -> list[dict]:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    rows = []

    for card in cards:
        href = clean_text(card.get("href"))
        product = clean_text(card.get("title"))
        text = clean_text(card.get("text")) or ""
        if not href or not product:
            continue

        sku = extract_sku(href)
        if not sku:
            continue

        current, regular = scraper._prices(text)
        availability = availability_fields(text=text)
        if current is None and availability["availability_status"] != UNAVAILABLE:
            continue

        rows.append(
            {
                "scrape_timestamp": now,
                "retailer": "Bodega Aurrera",
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
                "brand": scraper._infer_brand(
                    product,
                    clean_text(card.get("brand")),
                ),
                "product": product,
                "price_current": current,
                "price_regular": regular,
                "promotion": scraper._promotion(text),
                **availability,
                "pickup_available": None,
                "store_context_verified": False,
                "store_context_method": "manual_browser_raw_cdp",
                "url": urljoin(BASE_URL, href),
                "price_raw": text,
            }
        )

    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bodega Aurrera sobre Chrome existente usando CDP directo."
    )
    parser.add_argument("--category", default="cuidado-bucal")
    parser.add_argument("--ws-url", required=True)
    parser.add_argument("--output", default=str(OUTPUT))
    args = parser.parse_args()

    categories = {
        item.id: item
        for item in load_categories(
            ROOT / "config" / "bodega-aurrera" / "categories.yaml"
        )
    }
    category = categories.get(args.category)
    if category is None:
        raise SystemExit(f"Categoría no encontrada: {args.category}")

    location = next(
        item
        for item in load_locations(ROOT / "config" / "locations.yaml")
        if item.id == "bodega-aurrera-online"
    )

    scraper = BodegaAurreraScraper(headless=False)

    print("=" * 78)
    print("BODEGA AURRERA - CDP DIRECTO")
    print("=" * 78)
    print(f"Categoría : {category.id}")
    print(f"Destino   : {category.url}")
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

        navigate(cdp, session_id, category.url)
        print(f"Categoría abierta : {current_url(cdp, session_id)}")

        child_urls = content_child_urls(
            cdp,
            session_id,
            category.url,
        )
        sources = [category.url] + [
            url for url in child_urls if url != category.url
        ]

        all_rows: list[dict] = []
        seen_skus: set[str] = set()
        source_meta: list[dict] = []

        for source_index, source_url in enumerate(sources, start=1):
            navigate(cdp, session_id, source_url)
            initial_links = product_link_count(cdp, session_id)
            expansion = expand_current_page(cdp, session_id)
            pages = explicit_page_urls(cdp, session_id)

            source_rows = cards_to_rows(
                raw_cards(cdp, session_id),
                scraper,
                category,
                location,
            )

            page_meta = []
            for page_url in pages:
                navigate(cdp, session_id, page_url)
                page_expansion = expand_current_page(cdp, session_id)
                source_rows.extend(
                    cards_to_rows(
                        raw_cards(cdp, session_id),
                        scraper,
                        category,
                        location,
                    )
                )
                page_meta.append(
                    {
                        "url": page_url,
                        "product_links": page_expansion["product_links"],
                        "stabilized": page_expansion["stabilized"],
                    }
                )

            unique_source = {}
            for row in source_rows:
                sku = str(row.get("sku") or "").strip()
                if sku:
                    unique_source[sku] = row

            new_rows = []
            for sku, row in unique_source.items():
                if sku in seen_skus:
                    continue
                seen_skus.add(sku)
                new_rows.append(row)

            all_rows.extend(new_rows)

            meta = {
                "source_index": source_index,
                "source_url": source_url,
                "initial_product_links": initial_links,
                "product_links": expansion["product_links"],
                "rows": len(unique_source),
                "new_rows": len(new_rows),
                "scroll_rounds": len(expansion["rounds"]),
                "stabilized": expansion["stabilized"],
                "explicit_pages": len(pages),
                "explicit_page_meta": page_meta,
            }
            source_meta.append(meta)

            print(
                f"source={source_index}/{len(sources)} | "
                f"links={initial_links}->{expansion['product_links']} | "
                f"rows={len(unique_source)} | new={len(new_rows)} | "
                f"cumulative={len(all_rows)} | pages={len(pages)} | "
                f"stabilized={expansion['stabilized']}"
            )

        frame = pd.DataFrame(all_rows)
        for column in COLUMNS:
            if column not in frame.columns:
                frame[column] = None
        frame = frame[COLUMNS].copy()

        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = ROOT / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        DIAG.mkdir(parents=True, exist_ok=True)

        sku_complete = int(
            frame["sku"].fillna("").astype(str).str.strip().ne("").sum()
        ) if not frame.empty else 0
        price_complete = int(
            frame["price_current"].notna().sum()
        ) if not frame.empty else 0
        url_complete = int(
            frame["url"].fillna("").astype(str).str.strip().ne("").sum()
        ) if not frame.empty else 0

        pagination_verified = all(
            bool(item["stabilized"])
            for item in source_meta
            if item["product_links"] > 0
        )

        meta = {
            "retailer": "Bodega Aurrera",
            "category_id": category.id,
            "category_url": category.url,
            "content_child_urls": child_urls,
            "sources_discovered": len(sources),
            "sources": source_meta,
            "products": len(frame),
            "sku_complete": sku_complete,
            "price_complete": price_complete,
            "url_complete": url_complete,
            "pagination_verified": pagination_verified,
            "status": (
                "SUCCESS"
                if len(frame) > 0 and pagination_verified
                else "PARTIAL"
                if len(frame) > 0
                else "EMPTY"
            ),
        }

        (DIAG / f"{category.id}_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        summary = pd.DataFrame([meta | {"sources": None, "content_child_urls": None}])

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
        print("-" * 78)
        print(f"Status              : {meta['status']}")
        print(f"Productos           : {len(frame)}")
        print(f"SKU completos       : {sku_complete}")
        print(f"Precios completos   : {price_complete}")
        print(f"URLs completas      : {url_complete}")
        print(f"Fuentes             : {len(sources)}")
        print(f"Pagination verified : {pagination_verified}")
        print(f"Output              : {output_path}")

        return 0
    finally:
        cdp.close()


if __name__ == "__main__":
    raise SystemExit(main())
