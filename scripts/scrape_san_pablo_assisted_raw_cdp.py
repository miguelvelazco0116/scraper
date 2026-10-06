from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse, urlunparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import websocket
from openpyxl.styles import Font

from main import COLUMNS, CONSOLIDATED_PATH, update_consolidated_output
from scraper.availability import availability_fields
from scraper.config import load_categories, load_locations
from scraper.parsers import clean_text
from scraper.retailers.farmacias_san_pablo import FarmaciasSanPabloScraper

BASE_URL = "https://www.farmaciasanpablo.com.mx/"
OUTPUT = ROOT / "output" / "farmacias_san_pablo_assisted_raw_cdp.xlsx"
DIAG = ROOT / "diagnostics" / "farmacias_san_pablo_assisted_raw_cdp"


class RawCDP:
    def __init__(self, ws_url: str, timeout: float = 25.0) -> None:
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


def find_target(cdp: RawCDP) -> dict | None:
    targets = cdp.send("Target.getTargets").get("targetInfos") or []
    candidates = [
        item
        for item in targets
        if item.get("type") == "page"
        and "farmaciasanpablo.com.mx" in (item.get("url") or "").casefold()
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            0
            if (item.get("url") or "").rstrip("/")
            == BASE_URL.rstrip("/")
            else 1,
            item.get("url") or "",
        )
    )
    return candidates[0]


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
    raise TimeoutError("Farmacias San Pablo no termino de cargar.")


def is_verification(cdp: RawCDP, session_id: str) -> bool:
    blob = body_text(cdp, session_id).casefold()
    markers = (
        "access denied",
        "you don't have permission to access",
        "request rejected",
        "verify you are human",
        "verifica que eres humano",
        "captcha",
    )
    return any(marker in blob for marker in markers)


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
        "Farmacias San Pablo mostro una verificacion. "
        "Resuelvela manualmente en la pestana de Chrome."
    )
    print(
        "El scraper permanecera conectado y continuara automaticamente "
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
        "La verificacion manual de San Pablo no se resolvio dentro "
        f"de {int(timeout_seconds)} segundos."
    )


def navigate(cdp: RawCDP, session_id: str, url: str) -> None:
    cdp.send("Page.navigate", {"url": url}, session_id=session_id)
    wait_ready(cdp, session_id)
    time.sleep(1.2)
    wait_manual_verification(cdp, session_id)


def page_url(url: str, page_index: int) -> str:
    if page_index <= 0:
        return url
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["currentPage"] = [str(page_index)]
    encoded = urlencode(
        [(key, value) for key, values in query.items() for value in values]
    )
    return urlunparse(parsed._replace(query=encoded))


def target_count(cdp: RawCDP, session_id: str) -> int | None:
    text = body_text(cdp, session_id)
    values: list[int] = []
    for pattern in (
        r"\((\d{1,5})\s+resultados?\)",
        r"\b\d+\s*-\s*\d+\s+de\s+(\d{1,5})\b",
        r"\b(\d{1,5})\s+resultados?\b",
    ):
        values.extend(
            int(match)
            for match in re.findall(pattern, text, flags=re.IGNORECASE)
        )
    return max(values) if values else None


EXTRACT_JS = r"""
(() => {
  const normalize = value =>
    String(value || '').replace(/\s+/g, ' ').trim();
  const moneyRe = /\$\s*[0-9][0-9,]*(?:\.\d{1,2})?/;

  function addCount(root) {
    return Array.from(
      root.querySelectorAll('button, a, [role="button"]')
    ).filter(el =>
      /Agregar|Anadir|Añadir/i.test(
        normalize(el.innerText || el.textContent)
      )
    ).length;
  }

  function findCard(seed) {
    let node = seed;
    let fallback = null;

    for (let i = 0; i < 12 && node; i++, node = node.parentElement) {
      const text = normalize(node.innerText || node.textContent);
      if (!text || !moneyRe.test(text)) continue;
      if (text.length < 20 || text.length > 3200) continue;

      const adds = addCount(node);
      const images = node.querySelectorAll('img').length;
      const productLinks = new Set(
        Array.from(node.querySelectorAll('a[href*="/p/"]'))
          .map(a => a.href || a.getAttribute('href') || '')
          .filter(Boolean)
      );

      if (
        adds === 1
        || (
          adds === 0
          && images <= 4
          && productLinks.size <= 2
        )
      ) {
        return node;
      }

      if (!fallback) fallback = node;
    }

    return fallback || seed.parentElement || seed;
  }

  function readData(root) {
    const keys = [
      'data-product-code', 'data-product-id', 'data-code',
      'data-sku', 'data-ean', 'data-upc', 'data-id', 'data-item-id'
    ];
    const result = {};
    const nodes = [
      root,
      ...Array.from(root.querySelectorAll('*')).slice(0, 350)
    ];

    for (const el of nodes) {
      if (!el || !el.getAttribute) continue;
      for (const key of keys) {
        const value = el.getAttribute(key);
        if (value && !result[key]) result[key] = value;
      }
    }

    return result;
  }

  const seeds = [
    ...Array.from(document.querySelectorAll('a[href*="/p/"]')),
    ...Array.from(document.querySelectorAll(
      '[data-product-code], [data-product-id], [data-code], ' +
      '[data-sku], [data-ean], [data-upc], [data-item-id]'
    )),
    ...Array.from(
      document.querySelectorAll('button, a, [role="button"]')
    ).filter(el =>
      /Agregar|Anadir|Añadir/i.test(
        normalize(el.innerText || el.textContent)
      )
    )
  ];

  const out = [];
  const seen = new Set();

  for (const seed of seeds) {
    if (
      seed.closest &&
      seed.closest('header, nav, footer')
    ) {
      continue;
    }

    const card = findCard(seed);
    if (!card) continue;

    const rawText = String(card.innerText || card.textContent || '').trim();
    const text = normalize(rawText);
    if (!text || !moneyRe.test(text)) continue;

    const data = readData(card);

    const hrefs = [];
    if (seed.matches && seed.matches('a[href]')) {
      hrefs.push(seed.href || seed.getAttribute('href') || '');
    }
    hrefs.push(
      ...Array.from(card.querySelectorAll('a[href]'))
        .map(a => a.href || a.getAttribute('href') || '')
        .filter(Boolean)
    );

    const href =
      hrefs.find(h => /\/p\/\d+(?:[/?#]|$)/i.test(h))
      || '';

    const titleSelectors = [
      '[class*="product"][class*="name"]',
      '[class*="product"][class*="title"]',
      '[class*="name"]',
      '[class*="title"]',
      'h2', 'h3', 'h4', 'h5'
    ];

    let title = normalize(
      (seed.getAttribute && (
        seed.getAttribute('title')
        || seed.getAttribute('aria-label')
      )) || ''
    );

    for (const selector of titleSelectors) {
      if (title) break;
      const el = card.querySelector(selector);
      if (!el) continue;
      const candidate = normalize(el.innerText || el.textContent);
      if (
        candidate
        && candidate.length >= 5
        && candidate.length <= 220
        && !moneyRe.test(candidate)
        && !/Agregar|Anadir|Añadir|Descuento|GRATIS/i.test(candidate)
      ) {
        title = candidate;
      }
    }

    if (!title) {
      const img = card.querySelector('img[alt], img[title]');
      if (img) {
        title = normalize(
          img.getAttribute('alt') || img.getAttribute('title')
        );
      }
    }

    if (!title) {
      const lines = rawText
        .split(/\n+/)
        .map(normalize)
        .filter(Boolean);

      const candidates = lines.filter(line =>
        line.length >= 5
        && line.length <= 220
        && !moneyRe.test(line)
        && !/MXN|Agregar|Anadir|Añadir|Descuento|GRATIS|Ordenar por|Articulos por pagina|Artículos por página/i.test(line)
      );

      title = candidates[0] || '';
    }

    if (!title) continue;

    const key = [
      data['data-product-code'] || '',
      data['data-product-id'] || '',
      data['data-code'] || '',
      data['data-sku'] || '',
      data['data-ean'] || '',
      data['data-upc'] || '',
      href,
      title
    ].join('|');

    if (!key || seen.has(key)) continue;
    seen.add(key);

    out.push({
      productCode: data['data-product-code'] || '',
      productId: data['data-product-id'] || '',
      code: data['data-code'] || '',
      sku: data['data-sku'] || '',
      ean: data['data-ean'] || '',
      upc: data['data-upc'] || '',
      href,
      title,
      text: rawText
    });
  }

  return out;
})()
"""


def extract_cards(cdp: RawCDP, session_id: str) -> list[dict]:
    return cdp.evaluate(session_id, EXTRACT_JS) or []


def collect_page_cards(
    cdp: RawCDP,
    session_id: str,
    expected_on_page: int | None,
) -> tuple[list[dict], dict]:
    collected: dict[str, dict] = {}
    samples: list[dict] = []

    cdp.evaluate(session_id, "window.scrollTo(0, 0)")
    time.sleep(0.5)

    previous_total = 0
    stable_bottom_rounds = 0

    for step in range(90):
        cards = extract_cards(cdp, session_id)

        for card in cards:
            code = FarmaciasSanPabloScraper._code_from_card(card) or ""
            href = clean_text(card.get("href")) or ""
            title = clean_text(card.get("title")) or ""
            text = clean_text(card.get("text")) or ""
            key = code or href or f"{title}|{text[:180]}"
            if key:
                collected[key] = card

        total = len(collected)
        if step == 0 or step % 5 == 0 or total != previous_total:
            samples.append(
                {
                    "step": step,
                    "visible_cards": len(cards),
                    "cumulative_cards": total,
                }
            )

        if expected_on_page and total >= expected_on_page:
            break

        state = cdp.evaluate(
            session_id,
            """
            (() => {
              const y = window.scrollY || document.documentElement.scrollTop || 0;
              const h = Math.max(
                document.body.scrollHeight,
                document.documentElement.scrollHeight
              );
              const viewport =
                window.innerHeight || document.documentElement.clientHeight;
              window.scrollBy(
                0,
                Math.max(550, Math.floor(viewport * 0.72))
              );
              return {y, h, viewport};
            })()
            """,
        ) or {}

        time.sleep(0.35)

        new_state = cdp.evaluate(
            session_id,
            """
            (() => ({
              y: window.scrollY || document.documentElement.scrollTop || 0,
              h: Math.max(
                document.body.scrollHeight,
                document.documentElement.scrollHeight
              ),
              viewport:
                window.innerHeight || document.documentElement.clientHeight
            }))()
            """,
        ) or {}

        at_bottom = (
            int(new_state.get("y") or 0)
            + int(new_state.get("viewport") or 0)
            >= int(new_state.get("h") or 0) - 25
        )

        if at_bottom:
            if total <= previous_total:
                stable_bottom_rounds += 1
            else:
                stable_bottom_rounds = 0

            cdp.evaluate(
                session_id,
                "window.scrollTo(0, document.body.scrollHeight)",
            )
            time.sleep(0.5)

            if stable_bottom_rounds >= 3:
                break
        else:
            stable_bottom_rounds = 0

        previous_total = total

    cdp.evaluate(session_id, "window.scrollTo(0, 0)")

    return list(collected.values()), {
        "expected_on_page": expected_on_page,
        "cards_collected": len(collected),
        "scroll_samples": samples,
    }


def card_to_row(card: dict, category, location) -> dict | None:
    text = clean_text(card.get("text"))
    product = clean_text(card.get("title"))
    if not text or not product:
        return None

    current, regular, promotion = FarmaciasSanPabloScraper._prices_from_text(text)
    sku = FarmaciasSanPabloScraper._code_from_card(card)
    href = clean_text(card.get("href"))
    url = urljoin(BASE_URL, href) if href else None
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    availability = availability_fields(text=text)

    return {
        "scrape_timestamp": now,
        "retailer": "Farmacias San Pablo",
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
        "brand": FarmaciasSanPabloScraper._infer_brand(product, text),
        "product": product,
        "price_current": current,
        "price_regular": regular,
        "promotion": promotion,
        **availability,
        "pickup_available": None,
        "store_context_verified": False,
        "store_context_method": "assisted_raw_cdp_category_card",
        "url": url,
        "price_raw": text,
    }


def scrape_category(
    cdp: RawCDP,
    session_id: str,
    category,
    location,
    *,
    max_pages: int = 20,
    page_delay_seconds: float = 5.0,
) -> tuple[pd.DataFrame, dict]:
    unique_cards: dict[str, dict] = {}
    pages: list[dict] = []
    target: int | None = None
    no_new_pages = 0

    for page_index in range(max_pages):
        page_number = page_index + 1
        url = page_url(category.url, page_index)

        if page_index > 0:
            time.sleep(page_delay_seconds)

        navigate(cdp, session_id, url)

        page_target = target_count(cdp, session_id)
        if page_target:
            target = max(target or 0, page_target)

        expected_on_page = None
        if target:
            remaining = max(int(target) - (page_index * 48), 0)
            expected_on_page = min(48, remaining) if remaining else 0

        cards, hydration = collect_page_cards(
            cdp,
            session_id,
            expected_on_page,
        )

        before = len(unique_cards)
        for card in cards:
            code = FarmaciasSanPabloScraper._code_from_card(card) or ""
            href = clean_text(card.get("href")) or ""
            title = clean_text(card.get("title")) or ""
            text = clean_text(card.get("text")) or ""
            key = code or href or f"{title}|{text[:180]}"
            if key:
                unique_cards[key] = card

        after = len(unique_cards)
        new_count = after - before

        pages.append(
            {
                "page": page_number,
                "currentPage": page_index,
                "requested_url": url,
                "actual_url": current_url(cdp, session_id),
                "target_products": target,
                "cards_on_page": len(cards),
                "new_cards": new_count,
                "cumulative_cards": after,
                "hydration": hydration,
            }
        )

        print(
            f"  page={page_number} | cards={len(cards)} | "
            f"new={new_count} | cumulative={after} | target={target}"
        )

        if target and after >= target:
            break

        no_new_pages = no_new_pages + 1 if new_count == 0 else 0
        if no_new_pages >= 2:
            break

    recovery_search_url = None
    recovery_search_target = None
    recovery_cards = 0
    recovery_new = 0

    # If the published total is still not reached, make one additional
    # storefront search using the category term. This is a recovery pass,
    # not the primary source, and it is only used to fill a catalog gap.
    if target and len(unique_cards) < int(target):
        query = (
            clean_text(category.subcategory)
            or clean_text(category.name)
            or clean_text(category.id)
            or ""
        )
        if query:
            recovery_search_url = urljoin(
                BASE_URL,
                "search/" + quote(query, safe=""),
            )
            print(
                f"  RECOVERY_SEARCH: {len(unique_cards)}/{target}; "
                f"probando busqueda publica: {recovery_search_url}"
            )
            navigate(cdp, session_id, recovery_search_url)

            recovery_search_target = target_count(cdp, session_id)

            recovery_page_cards, recovery_hydration = collect_page_cards(
                cdp,
                session_id,
                expected_on_page=None,
            )
            recovery_cards = len(recovery_page_cards)
            before_recovery = len(unique_cards)

            for card in recovery_page_cards:
                code = FarmaciasSanPabloScraper._code_from_card(card) or ""
                href = clean_text(card.get("href")) or ""
                title = clean_text(card.get("title")) or ""
                text = clean_text(card.get("text")) or ""
                key = code or href or f"{title}|{text[:180]}"
                if key:
                    unique_cards[key] = card

            recovery_new = len(unique_cards) - before_recovery
            pages.append(
                {
                    "page": "recovery-search",
                    "requested_url": recovery_search_url,
                    "actual_url": current_url(cdp, session_id),
                    "target_products": target,
                    "cards_on_page": recovery_cards,
                    "new_cards": recovery_new,
                    "cumulative_cards": len(unique_cards),
                    "hydration": recovery_hydration,
                }
            )
            print(
                f"  RECOVERY_RESULT: cards={recovery_cards} | "
                f"new={recovery_new} | cumulative={len(unique_cards)} | "
                f"target={target}"
            )

    rows = [
        row
        for card in unique_cards.values()
        if (row := card_to_row(card, category, location)) is not None
    ]

    unique_rows: dict[str, dict] = {}
    for row in rows:
        key = (
            clean_text(row.get("sku"))
            or clean_text(row.get("url"))
            or "|".join(
                [
                    clean_text(row.get("product")) or "",
                    str(row.get("price_current") or ""),
                    clean_text(row.get("price_raw")) or "",
                ]
            )
        )
        if key:
            unique_rows[key] = row

    frame = pd.DataFrame(list(unique_rows.values()))
    for column in COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    frame = frame[COLUMNS].copy()

    products = len(frame)
    coverage = (products / target) if target else None
    price_complete = (
        int(frame["price_current"].notna().sum())
        if not frame.empty else 0
    )
    sku_complete = (
        int(
            frame["sku"]
            .fillna("")
            .astype(str)
            .str.strip()
            .ne("")
            .sum()
        )
        if not frame.empty else 0
    )
    url_complete = (
        int(
            frame["url"]
            .fillna("")
            .astype(str)
            .str.strip()
            .ne("")
            .sum()
        )
        if not frame.empty else 0
    )

    min_coverage = 0.80
    if products <= 0:
        status = "EMPTY"
        coverage_status = "EMPTY"
    elif price_complete < products:
        status = "PARTIAL"
        coverage_status = "PRICE_GAP"
    elif coverage is not None and coverage < min_coverage:
        status = "PARTIAL"
        coverage_status = "BELOW_THRESHOLD"
    elif coverage is not None and coverage < 1.0:
        status = "SUCCESS"
        coverage_status = "SAMPLE_ACCEPTED"
    else:
        status = "SUCCESS"
        coverage_status = "COMPLETE"

    meta = {
        "category_id": category.id,
        "status": status,
        "target_products": target,
        "products": products,
        "coverage": coverage,
        "min_accepted_coverage": min_coverage,
        "coverage_status": coverage_status,
        "sku_complete": sku_complete,
        "price_complete": price_complete,
        "url_complete": url_complete,
        "pages_scanned": len(pages),
        "recovery_search_url": recovery_search_url,
        "recovery_search_target": recovery_search_target,
        "recovery_cards": recovery_cards,
        "recovery_new": recovery_new,
        "pages": pages,
        "engine": "existing Chrome + raw CDP + category cards",
    }

    return frame, meta


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Farmacias San Pablo asistido sobre Chrome existente."
    )
    parser.add_argument("--category", default="descongestionantes")
    parser.add_argument("--ws-url", required=True)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument(
        "--update-consolidated",
        action="store_true",
    )
    args = parser.parse_args()

    categories = {
        item.id: item
        for item in load_categories(
            ROOT / "config" / "farmacias-san-pablo" / "categories.yaml"
        )
    }
    category = categories.get(args.category)
    if category is None:
        raise SystemExit(
            f"Categoria San Pablo no encontrada: {args.category}"
        )

    location = next(
        item
        for item in load_locations(ROOT / "config" / "locations.yaml")
        if item.id == "san-pablo-online"
    )

    print("=" * 82)
    print("FARMACIAS SAN PABLO - SESION ASISTIDA CDP")
    print("=" * 82)
    print(f"Categoria : {category.id}")
    print(f"Destino   : {category.url}")
    print("")

    cdp = RawCDP(args.ws_url)
    try:
        target = find_target(cdp)
        if target is None:
            print(
                "ERROR: no se encontro una pestana abierta "
                "de Farmacias San Pablo."
            )
            return 1

        print(f"Pestana detectada: {target.get('url')}")

        attached = cdp.send(
            "Target.attachToTarget",
            {"targetId": target["targetId"], "flatten": True},
        )
        session_id = attached["sessionId"]
        cdp.send("Runtime.enable", session_id=session_id)
        cdp.send("Page.enable", session_id=session_id)

        wait_manual_verification(cdp, session_id)

        frame, meta = scrape_category(
            cdp,
            session_id,
            category,
            location,
            max_pages=args.max_pages,
        )

        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        DIAG.mkdir(parents=True, exist_ok=True)

        with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
            frame.to_excel(writer, index=False, sheet_name="Concentrado")
            pd.DataFrame(
                [{**meta, "pages": None}]
            ).to_excel(writer, index=False, sheet_name="Resumen")

            for sheet_name in ("Concentrado", "Resumen"):
                ws = writer.book[sheet_name]
                ws.freeze_panes = "A2"
                ws.auto_filter.ref = ws.dimensions
                for cell in ws[1]:
                    cell.font = Font(bold=True)

        (DIAG / f"{category.id}_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if args.update_consolidated and meta["status"] == "SUCCESS":
            update_consolidated_output(
                frame,
                ROOT / CONSOLIDATED_PATH,
            )

        print("")
        print("RESULTADO")
        print("-" * 82)
        print(f"Status              : {meta['status']}")
        print(f"Productos           : {meta['products']}")
        print(f"Total publicado     : {meta['target_products']}")
        print(f"Cobertura publicada : {meta['coverage']}")
        print(f"Estado cobertura     : {meta['coverage_status']}")
        print(f"SKU completos       : {meta['sku_complete']}")
        print(f"Precios completos   : {meta['price_complete']}")
        print(f"URLs completas      : {meta['url_complete']}")
        print(f"Paginas recorridas  : {meta['pages_scanned']}")
        print(f"Recovery new        : {meta['recovery_new']}")
        print(f"Output              : {OUTPUT}")
        print(
            "Consolidado          : "
            + (
                "actualizado"
                if args.update_consolidated
                and meta["status"] == "SUCCESS"
                else "sin cambios"
            )
        )

        return 0 if meta["status"] == "SUCCESS" else 2
    finally:
        cdp.close()


if __name__ == "__main__":
    raise SystemExit(main())
