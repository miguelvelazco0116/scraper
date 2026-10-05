from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import websocket
from openpyxl.styles import Font

from main import COLUMNS
from scraper.config import load_categories, load_locations
from scraper.parsers import clean_text
from scraper.retailers.farmacias_guadalajara import (
    BASE_URL,
    FarmaciasGuadalajaraScraper,
)

OUTPUT = ROOT / "output" / "farmacias_guadalajara_raw_cdp.xlsx"
DIAG = ROOT / "diagnostics" / "farmacias_guadalajara_raw_cdp"


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
        payload: dict = {"id": request_id, "method": method}
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
        remote = result.get("result") or {}
        if result.get("exceptionDetails"):
            raise RuntimeError(
                f"Runtime.evaluate failed: {result['exceptionDetails']}"
            )
        return remote.get("value")


def find_fg_target(cdp: RawCDP) -> dict | None:
    targets = cdp.send("Target.getTargets").get("targetInfos") or []
    candidates = [
        item
        for item in targets
        if item.get("type") == "page"
        and "farmaciasguadalajara.com" in (item.get("url") or "").casefold()
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            0
            if (item.get("url") or "").rstrip("/")
            == "https://www.farmaciasguadalajara.com"
            else 1,
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
    last_state = None
    while time.monotonic() < deadline:
        try:
            last_state = cdp.evaluate(session_id, "document.readyState")
        except Exception:
            last_state = None

        if last_state in {"interactive", "complete"}:
            time.sleep(1)
            return
        time.sleep(0.5)

    raise TimeoutError(
        f"La página no terminó de cargar. readyState={last_state!r}"
    )


def body_text(cdp: RawCDP, session_id: str) -> str:
    value = cdp.evaluate(
        session_id,
        "document.body ? document.body.innerText : ''",
    )
    return str(value or "")


def current_url(cdp: RawCDP, session_id: str) -> str:
    return str(cdp.evaluate(session_id, "location.href") or "")


def assert_not_blocked(cdp: RawCDP, session_id: str) -> None:
    text = body_text(cdp, session_id).casefold()
    markers = (
        "access denied",
        "verifica que eres humano",
        "verify you are human",
        "captcha",
        "request rejected",
    )
    if any(marker in text for marker in markers):
        raise RuntimeError(
            "Farmacias Guadalajara presentó un bloqueo o verificación."
        )


def target_count(cdp: RawCDP, session_id: str) -> int | None:
    matches = re.findall(
        r"\(?\b(\d{1,5})\s+productos?\b\)?",
        body_text(cdp, session_id),
        flags=re.IGNORECASE,
    )
    if not matches:
        return None
    return max(int(value) for value in matches)


def product_link_count(cdp: RawCDP, session_id: str) -> int:
    value = cdp.evaluate(
        session_id,
        r"""
        (() => {
          const re = /-\d{5,14}\.html(?:$|[?#])/i;
          const hrefs = Array.from(
            document.querySelectorAll('a[href*=".html"]')
          )
            .map(a => a.href || '')
            .filter(h => re.test(h));
          return new Set(hrefs).size;
        })()
        """,
    )
    return int(value or 0)


def _dispatch_mouse_click(
    cdp: RawCDP,
    session_id: str,
    x: float,
    y: float,
) -> None:
    cdp.send(
        "Input.dispatchMouseEvent",
        {
            "type": "mouseMoved",
            "x": x,
            "y": y,
            "button": "none",
        },
        session_id=session_id,
    )
    time.sleep(0.12)
    cdp.send(
        "Input.dispatchMouseEvent",
        {
            "type": "mousePressed",
            "x": x,
            "y": y,
            "button": "left",
            "buttons": 1,
            "clickCount": 1,
        },
        session_id=session_id,
    )
    time.sleep(0.08)
    cdp.send(
        "Input.dispatchMouseEvent",
        {
            "type": "mouseReleased",
            "x": x,
            "y": y,
            "button": "left",
            "buttons": 0,
            "clickCount": 1,
        },
        session_id=session_id,
    )


def click_load_more(
    cdp: RawCDP,
    session_id: str,
    previous_count: int,
) -> dict:
    candidates = cdp.evaluate(
        session_id,
        r"""
        (() => {
          const norm = value => String(value || '')
            .replace(/\s+/g, ' ')
            .trim()
            .toLowerCase();

          const textBlob = el => [
            el.innerText,
            el.textContent,
            el.getAttribute && el.getAttribute('aria-label'),
            el.getAttribute && el.getAttribute('title'),
            el.value
          ]
            .filter(Boolean)
            .map(norm)
            .join(' | ');

          const matches = el => {
            const blob = textBlob(el);
            return blob.includes('ver más productos')
              || blob.includes('ver mas productos')
              || blob.includes('mostrar los siguientes');
          };

          const visible = el => {
            if (!el || !el.getBoundingClientRect) return false;
            const style = getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style.display !== 'none'
              && style.visibility !== 'hidden'
              && Number(style.opacity || 1) !== 0
              && rect.width > 3
              && rect.height > 3;
          };

          const depth = el => {
            let d = 0;
            let node = el;
            while (node && node.parentElement) {
              d++;
              node = node.parentElement;
            }
            return d;
          };

          const score = el => {
            const rect = el.getBoundingClientRect();
            const blob = textBlob(el);
            const exact =
              blob === 'ver más productos'
              || blob === 'ver mas productos'
              || blob.startsWith('ver más productos |')
              || blob.startsWith('ver mas productos |');

            const interactive =
              el.matches('button, a, [role="button"], input[type="button"], input[type="submit"]')
              || typeof el.onclick === 'function'
              || el.hasAttribute('onclick')
              || getComputedStyle(el).cursor === 'pointer';

            return {
              exact,
              interactive,
              area: rect.width * rect.height,
              depth: depth(el)
            };
          };

          const raw = Array.from(document.querySelectorAll(
            'button, a, [role="button"], input[type="button"], input[type="submit"], span, div, p'
          ))
            .filter(el => visible(el) && matches(el))
            .map(el => {
              let clickable = el.closest(
                'button, a, [role="button"], input[type="button"], input[type="submit"]'
              );

              if (!clickable) {
                let parent = el;
                for (
                  let i = 0;
                  i < 8 && parent;
                  i++, parent = parent.parentElement
                ) {
                  if (
                    typeof parent.onclick === 'function'
                    || parent.hasAttribute('onclick')
                    || getComputedStyle(parent).cursor === 'pointer'
                  ) {
                    clickable = parent;
                    break;
                  }
                }
              }

              clickable = clickable || el;
              if (!visible(clickable)) return null;

              const rect = clickable.getBoundingClientRect();
              const s = score(clickable);

              return {
                tag: clickable.tagName,
                text: textBlob(clickable).slice(0, 180),
                x: rect.left + rect.width / 2,
                y: rect.top + rect.height / 2,
                width: rect.width,
                height: rect.height,
                area: s.area,
                depth: s.depth,
                exact: s.exact,
                interactive: s.interactive
              };
            })
            .filter(Boolean);

          const unique = [];
          const seen = new Set();
          for (const item of raw) {
            const key = [
              item.tag,
              Math.round(item.x),
              Math.round(item.y),
              Math.round(item.width),
              Math.round(item.height)
            ].join('|');
            if (seen.has(key)) continue;
            seen.add(key);
            unique.push(item);
          }

          unique.sort((a, b) => {
            if (a.exact !== b.exact) return a.exact ? -1 : 1;
            if (a.interactive !== b.interactive) return a.interactive ? -1 : 1;
            if (a.area !== b.area) return a.area - b.area;
            return b.depth - a.depth;
          });

          return unique.slice(0, 12);
        })()
        """,
    ) or []

    if not candidates:
        return {
            "clicked": False,
            "reason": "load_more_not_found",
            "candidates": [],
        }

    attempts: list[dict] = []

    for index, candidate in enumerate(candidates, start=1):
        try:
            # Recenter the intended candidate immediately before input.
            cdp.evaluate(
                session_id,
                f"""
                (() => {{
                  const x = {float(candidate['x'])};
                  const y = {float(candidate['y'])};
                  const el = document.elementFromPoint(x, y);
                  if (el) {{
                    el.scrollIntoView({{
                      block: 'center',
                      inline: 'center',
                      behavior: 'instant'
                    }});
                  }}
                  return true;
                }})()
                """,
            )
        except Exception:
            pass

        time.sleep(0.2)

        # Coordinates may shift slightly after centering; resolve the best
        # exact/smallest candidate again and use its current viewport center.
        refreshed = cdp.evaluate(
            session_id,
            r"""
            (() => {
              const norm = value => String(value || '')
                .replace(/\s+/g, ' ')
                .trim()
                .toLowerCase();

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
                'button, a, [role="button"], input[type="button"], input[type="submit"], span, div, p'
              ))
                .filter(el => {
                  if (!visible(el)) return false;
                  const text = norm(
                    el.innerText
                    || el.textContent
                    || el.getAttribute('aria-label')
                    || el.getAttribute('title')
                    || el.value
                  );
                  return text === 'ver más productos'
                    || text === 'ver mas productos';
                })
                .map(el => {
                  let clickable = el.closest(
                    'button, a, [role="button"], input[type="button"], input[type="submit"]'
                  ) || el;
                  const rect = clickable.getBoundingClientRect();
                  return {
                    tag: clickable.tagName,
                    text: norm(
                      clickable.innerText
                      || clickable.textContent
                      || clickable.getAttribute('aria-label')
                      || clickable.getAttribute('title')
                      || clickable.value
                    ).slice(0, 180),
                    x: rect.left + rect.width / 2,
                    y: rect.top + rect.height / 2,
                    width: rect.width,
                    height: rect.height,
                    area: rect.width * rect.height
                  };
                })
                .sort((a, b) => a.area - b.area);

              return nodes[0] || null;
            })()
            """,
        )

        point = refreshed or candidate
        x = float(point["x"])
        y = float(point["y"])

        hit = cdp.evaluate(
            session_id,
            f"""
            (() => {{
              const el = document.elementFromPoint({x}, {y});
              if (!el) return null;
              const rect = el.getBoundingClientRect();
              return {{
                tag: el.tagName,
                text: String(el.innerText || el.textContent || '')
                  .replace(/\\s+/g, ' ')
                  .trim()
                  .slice(0, 180),
                x: rect.left + rect.width / 2,
                y: rect.top + rect.height / 2
              }};
            }})()
            """,
        )

        # Click the actual topmost hit element at the candidate point.
        click_x = float(hit["x"]) if hit and hit.get("x") is not None else x
        click_y = float(hit["y"]) if hit and hit.get("y") is not None else y
        _dispatch_mouse_click(cdp, session_id, click_x, click_y)

        deadline = time.monotonic() + 5.0
        current = previous_count
        while time.monotonic() < deadline:
            time.sleep(0.35)
            current = product_link_count(cdp, session_id)
            if current > previous_count:
                return {
                    "clicked": True,
                    "mode": "cdp_mouse_ranked",
                    "candidate_index": index,
                    "candidate": candidate,
                    "hit": hit,
                    "after": current,
                    "attempts": attempts,
                }

        attempts.append(
            {
                "candidate_index": index,
                "candidate": candidate,
                "hit": hit,
                "after": current,
            }
        )

    return {
        "clicked": False,
        "reason": "all_load_more_candidates_failed",
        "candidates": candidates,
        "attempts": attempts,
    }


def expand_catalog(
    cdp: RawCDP,
    session_id: str,
    target: int | None,
    max_rounds: int,
) -> list[dict]:
    previous = product_link_count(cdp, session_id)
    trace: list[dict] = []

    for round_number in range(1, max_rounds + 1):
        if target and previous >= target:
            break

        cdp.evaluate(
            session_id,
            "window.scrollTo(0, document.body.scrollHeight)",
        )
        time.sleep(0.8)

        click = click_load_more(cdp, session_id, previous)
        if not click.get("clicked"):
            trace.append(
                {
                    "round": round_number,
                    "before": previous,
                    "after": previous,
                    "clicked": False,
                    "reason": "load_more_not_found",
                }
            )
            break

        current = int(click.get("after") or product_link_count(cdp, session_id))

        trace.append(
            {
                "round": round_number,
                "before": previous,
                "after": current,
                "clicked": True,
                "mode": click.get("mode"),
                "candidate_index": click.get("candidate_index"),
                "candidate": click.get("candidate"),
                "hit": click.get("hit"),
            }
        )

        if current <= previous:
            break

        previous = current

    return trace


def extract_cards(cdp: RawCDP, session_id: str) -> list[dict]:
    value = cdp.evaluate(
        session_id,
        r"""
        (() => {
          const out = [];
          const seen = new Set();
          const productRe = /-\d{5,14}\.html(?:$|[?#])/i;
          const moneyRe = /\$\s*[0-9][0-9,]*(?:\.\d{1,2})?/;

          for (const a of Array.from(
            document.querySelectorAll('a[href*=".html"]')
          )) {
            const href = a.href || '';
            if (!productRe.test(href) || seen.has(href)) continue;

            let node = a;
            let card = null;
            for (let i = 0; i < 9 && node; i++, node = node.parentElement) {
              const text = (node.innerText || '').trim();
              if (
                moneyRe.test(text)
                && text.length >= 15
                && text.length <= 3000
              ) {
                card = node;
                if (/Agregar|Comparar|Favoritos/i.test(text)) break;
              }
            }

            const source = card || a.parentElement || a;
            const text = (source.innerText || '').trim();
            if (!moneyRe.test(text)) continue;

            const named = source.querySelector('[class*="brand" i]');
            const titleNode = source.querySelector(
              'h2, h3, h4, [class*="name" i], [class*="title" i]'
            );

            let name = (
              a.innerText
              || a.getAttribute('aria-label')
              || a.getAttribute('title')
              || ''
            ).trim();

            if (!name && titleNode) {
              name = (titleNode.innerText || '').trim();
            }

            if (!name) {
              const lines = text
                .split(/\n+/)
                .map(x => x.trim())
                .filter(Boolean);

              name = lines.find(
                x =>
                  !moneyRe.test(x)
                  && !/Agregar|Comparar|Oferta|Favoritos/i.test(x)
                  && x.length > 8
              ) || '';
            }

            if (!name) continue;

            seen.add(href);
            out.push({
              href,
              name,
              brand: named ? (named.innerText || '').trim() : '',
              text,
              dataPid:
                source.getAttribute('data-product-id')
                || source.getAttribute('data-part-number')
                || a.getAttribute('data-product-id')
                || ''
            });
          }

          return out;
        })()
        """,
    )
    return value or []


def normalize_frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    for column in COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    return frame[COLUMNS].copy()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Farmacias Guadalajara sobre Chrome existente usando CDP directo."
    )
    parser.add_argument("--category", default="cuidado-bucal")
    parser.add_argument("--ws-url", required=True)
    parser.add_argument("--max-load-more", type=int, default=100)
    args = parser.parse_args()

    categories = {
        item.id: item
        for item in load_categories(
            ROOT / "config" / "farmacias-guadalajara" / "categories.yaml"
        )
    }
    category = categories.get(args.category)
    if category is None:
        raise SystemExit(f"Categoría no encontrada: {args.category}")

    location = next(
        item
        for item in load_locations(ROOT / "config" / "locations.yaml")
        if item.id == "fg-online"
    )

    scraper = FarmaciasGuadalajaraScraper(
        headless=False,
        max_load_more=args.max_load_more,
    )

    print("=" * 78)
    print("FARMACIAS GUADALAJARA - CDP DIRECTO")
    print("=" * 78)
    print(f"Categoría : {category.id}")
    print(f"Destino   : {category.url}")
    print("")

    cdp = RawCDP(args.ws_url)
    try:
        target = find_fg_target(cdp)
        if target is None:
            print("ERROR: no se encontró una pestaña abierta de Farmacias Guadalajara.")
            return 1

        print(f"Pestaña detectada: {target.get('url')}")

        attached = cdp.send(
            "Target.attachToTarget",
            {"targetId": target["targetId"], "flatten": True},
        )
        session_id = attached["sessionId"]

        cdp.send("Runtime.enable", session_id=session_id)
        cdp.send("Page.enable", session_id=session_id)

        cdp.send(
            "Page.navigate",
            {"url": category.url},
            session_id=session_id,
        )
        wait_ready(cdp, session_id)
        assert_not_blocked(cdp, session_id)

        print(f"Categoría abierta : {current_url(cdp, session_id)}")

        target_products = target_count(cdp, session_id)
        initial_links = product_link_count(cdp, session_id)

        print(f"Target publicado   : {target_products}")
        print(f"Links iniciales    : {initial_links}")

        trace = expand_catalog(
            cdp,
            session_id,
            target_products,
            args.max_load_more,
        )
        cards = extract_cards(cdp, session_id)

        now = datetime.now().astimezone().isoformat(timespec="seconds")
        rows: list[dict] = []

        for card in cards:
            url = urljoin(BASE_URL, card.get("href") or "")
            sku = clean_text(card.get("dataPid")) or scraper.extract_sku(url)
            product = clean_text(card.get("name"))
            if not sku or not product:
                continue

            current, regular, promotion = scraper._prices_from_text(
                card.get("text")
            )
            if current is None:
                continue

            brand = clean_text(card.get("brand")) or scraper._infer_brand(
                product,
                card.get("text"),
            )

            rows.append(
                {
                    "scrape_timestamp": now,
                    "retailer": "Farmacias Guadalajara",
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
                    "brand": brand,
                    "product": product,
                    "price_current": current,
                    "price_regular": regular,
                    "promotion": promotion,
                    "pickup_available": None,
                    "store_context_verified": False,
                    "store_context_method": "manual_browser_raw_cdp",
                    "url": url,
                    "price_raw": clean_text(card.get("text")),
                }
            )

        rows = list({(row["sku"], row["url"]): row for row in rows}.values())
        frame = normalize_frame(rows)
        final_links = product_link_count(cdp, session_id)

        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        DIAG.mkdir(parents=True, exist_ok=True)

        meta = {
            "category_id": category.id,
            "category_url": category.url,
            "target_products": target_products,
            "initial_product_links": initial_links,
            "product_links": final_links,
            "rows": len(frame),
            "expansion_trace": trace,
            "browser_target_id": target.get("targetId"),
        }
        (DIAG / f"{category.id}_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        summary = pd.DataFrame(
            [{
                "retailer": "Farmacias Guadalajara",
                "category_id": category.id,
                "target_products": target_products,
                "product_links": final_links,
                "products": len(frame),
                "coverage": (
                    len(frame) / target_products
                    if target_products
                    else None
                ),
                "sku_complete": int(
                    frame["sku"].fillna("").astype(str).str.strip().ne("").sum()
                ),
                "price_complete": int(frame["price_current"].notna().sum()),
                "url_complete": int(
                    frame["url"].fillna("").astype(str).str.strip().ne("").sum()
                ),
            }]
        )

        with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
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
        print(f"Target publicado   : {target_products}")
        print(f"Links detectados   : {final_links}")
        print(f"Productos extraídos: {len(frame)}")
        print(f"SKU completos      : {int(frame['sku'].fillna('').astype(str).str.strip().ne('').sum())}")
        print(f"Precios completos  : {int(frame['price_current'].notna().sum())}")
        print(f"URLs completas     : {int(frame['url'].fillna('').astype(str).str.strip().ne('').sum())}")
        print(f"Rondas expansión   : {len(trace)}")
        for item in trace:
            print(
                f"  ronda {item.get('round')}: "
                f"{item.get('before')} -> {item.get('after')} | "
                f"clicked={item.get('clicked')} | "
                f"{item.get('mode') or item.get('reason') or ''}"
            )
        print(f"Output             : {OUTPUT}")
        return 0
    finally:
        cdp.close()


if __name__ == "__main__":
    raise SystemExit(main())
