from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from ..config import Category, Location
from ..parsers import clean_text, extract_sku


BASE_URL = "https://www.bodegaaurrera.com.mx/"
DIAGNOSTICS = Path("diagnostics")

BLOCK_MARKERS = (
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


class BodegaAurreraBlocked(RuntimeError):
    pass


class BodegaAurreraNetworkUnavailable(RuntimeError):
    pass


class BodegaAurreraScraper:
    """Scraper inicial del catálogo público de Bodega Aurrera.

    Bodega Aurrera comparte infraestructura con Walmart y puede presentar
    desafíos de identidad. Este scraper no resuelve CAPTCHAs ni intenta evadir
    controles anti-bot. En navegador visible únicamente espera a que el usuario
    complete manualmente una verificación, si el sitio la presenta.
    """

    MONEY_RE = re.compile(r"\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)")
    CURRENT_RE = re.compile(
        r"precio\s+actual\s*\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)",
        re.IGNORECASE,
    )
    BEFORE_RE = re.compile(
        r"Antes\s*\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)",
        re.IGNORECASE,
    )

    BRAND_CANDIDATES = (
        "Colgate",
        "Oral-B",
        "Oral B",
        "Listerine",
        "Sensodyne",
        "Crest",
        "Equate",
        "GUM",
        "Corega",
        "Aquafresh",
        "Parodontax",
        "Curaprox",
        "Philips",
        "Reach",
        "Pro",
    )

    def __init__(
        self,
        headless: bool = False,
        browser_channel: str | None = "chrome",
        max_pages: int = 30,
        wait_ms: int = 1200,
        manual_verification_timeout_ms: int = 180_000,
    ) -> None:
        self.headless = headless
        self.browser_channel = browser_channel
        self.max_pages = max_pages
        self.wait_ms = wait_ms
        self.manual_verification_timeout_ms = manual_verification_timeout_ms
        self.run_meta: dict[str, Any] = {}

    @staticmethod
    def _paged_url(url: str, page_number: int) -> str:
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        if page_number <= 1:
            query.pop("page", None)
        else:
            query["page"] = str(page_number)
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )

    @staticmethod
    def _normalize(value: str | None) -> str:
        if not value:
            return ""
        normalized = value.casefold()
        normalized = (
            normalized.replace("á", "a")
            .replace("é", "e")
            .replace("í", "i")
            .replace("ó", "o")
            .replace("ú", "u")
            .replace("ü", "u")
        )
        return re.sub(r"\s+", " ", normalized).strip()

    @staticmethod
    def _body_text(page) -> str:
        try:
            return page.locator("body").inner_text(timeout=8_000)
        except Exception:
            return ""

    def _save_diagnostics(self, page, prefix: str) -> None:
        DIAGNOSTICS.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", prefix)

        try:
            page.screenshot(
                path=str(DIAGNOSTICS / f"bodega_aurrera_{safe}.png"),
                full_page=True,
            )
        except Exception:
            pass

        try:
            (DIAGNOSTICS / f"bodega_aurrera_{safe}.html").write_text(
                page.content(),
                encoding="utf-8",
            )
        except Exception:
            pass

        try:
            (DIAGNOSTICS / f"bodega_aurrera_{safe}.json").write_text(
                json.dumps(self.run_meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _is_blocked(self, page, status: int | None = None) -> bool:
        body = self._normalize(self._body_text(page))
        blocked_url = "/blocked" in (page.url or "").casefold()
        return bool(
            status in (401, 403, 429)
            or blocked_url
            or any(self._normalize(marker) in body for marker in BLOCK_MARKERS)
        )

    def _assert_not_blocked(self, page, status: int | None = None) -> None:
        if not self._is_blocked(page, status):
            return

        self.run_meta["blocked_detected"] = True
        self.run_meta["blocked_status"] = status
        self.run_meta["blocked_url"] = page.url
        self._save_diagnostics(page, "blocked")

        if not self.headless:
            self.run_meta["manual_verification_required"] = True
            self.run_meta["manual_verification_resolved"] = False
            print(
                "MANUAL_VERIFICATION_REQUIRED: Bodega Aurrera mostró una "
                "verificación de identidad. Complétala manualmente en Chrome; "
                "el scraper esperará hasta 3 minutos.",
                flush=True,
            )

            deadline = time.monotonic() + (
                self.manual_verification_timeout_ms / 1000
            )
            while time.monotonic() < deadline:
                page.wait_for_timeout(1_000)
                if not self._is_blocked(page):
                    self.run_meta["manual_verification_resolved"] = True
                    self._save_diagnostics(
                        page,
                        "manual_verification_resolved",
                    )
                    print(
                        "MANUAL_VERIFICATION_RESOLVED: continuando.",
                        flush=True,
                    )
                    return

        raise BodegaAurreraBlocked(
            "Bodega Aurrera bloqueó o desafió la sesión automatizada. "
            "Se guardaron diagnósticos; no se intenta evadir la protección."
        )

    def _goto(self, page, url: str) -> None:
        try:
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=120_000,
            )
        except PlaywrightError as exc:
            raise BodegaAurreraNetworkUnavailable(str(exc)) from exc

        status = response.status if response else None
        page.wait_for_timeout(self.wait_ms)
        self._assert_not_blocked(page, status)

    @staticmethod
    def _raw_cards(page) -> list[dict]:
        return page.locator("body").evaluate(
            r"""
            () => {
              const normalize = value =>
                String(value || '').replace(/\s+/g, ' ').trim();

              const out = [];
              const seen = new Set();

              for (const a of Array.from(
                document.querySelectorAll('a[href*="/ip/"]')
              )) {
                const href = a.href || '';
                if (!href || seen.has(href)) continue;

                // Sponsored brand banners can contain real /ip/ links, prices,
                // and a generic "Ver más" CTA. They are not catalogue cards
                // and must never be counted as category coverage.
                let sponsored = false;
                let sponsorProbe = a;
                for (
                  let depth = 0;
                  depth < 10 && sponsorProbe;
                  depth++, sponsorProbe = sponsorProbe.parentElement
                ) {
                  const probeText = normalize(
                    sponsorProbe.innerText || sponsorProbe.textContent
                  ).toLowerCase();
                  const probeClass = normalize(
                    sponsorProbe.getAttribute('class')
                  ).toLowerCase();
                  const probeTestId = normalize(
                    sponsorProbe.getAttribute('data-testid') ||
                    sponsorProbe.getAttribute('data-automation-id')
                  ).toLowerCase();

                  if (
                    probeText.includes('patrocinado por') ||
                    probeText.includes('sponsored by') ||
                    probeClass.includes('sponsor') ||
                    probeTestId.includes('sponsor')
                  ) {
                    sponsored = true;
                    break;
                  }
                }
                if (sponsored) continue;

                let card = a;
                let found = null;

                for (let i = 0; i < 12 && card; i++, card = card.parentElement) {
                  const text = normalize(card.innerText || card.textContent);
                  if (
                    text &&
                    /\$\s*[0-9][0-9,]*(?:\.\d{1,2})?/.test(text) &&
                    text.length >= 10 &&
                    text.length <= 3500
                  ) {
                    const productLinks = card.querySelectorAll(
                      'a[href*="/ip/"]'
                    ).length;

                    if (productLinks <= 3) {
                      found = card;
                      break;
                    }
                  }
                }

                if (!found) continue;

                const rawText = (found.innerText || found.textContent || '').trim();

                const heading = found.querySelector(
                  'h1,h2,h3,h4,h5,[class*="name"],[class*="title"]'
                );
                const image = found.querySelector('img[alt]');
                const brandNode = found.querySelector(
                  '[data-automation-id*="brand"], [class*="brand"]'
                );

                const title =
                  normalize(a.getAttribute('aria-label')) ||
                  normalize(a.getAttribute('title')) ||
                  normalize(a.innerText) ||
                  normalize(heading ? heading.innerText : '') ||
                  normalize(image ? image.alt : '');

                const brand = normalize(
                  brandNode ? brandNode.innerText || brandNode.textContent : ''
                );

                seen.add(href);
                out.push({
                  href,
                  title,
                  brand,
                  text: rawText,
                });
              }

              return out;
            }
            """
        )

    @classmethod
    def _prices(
        cls,
        text: str | None,
    ) -> tuple[float | None, float | None]:
        blob = text or ""

        current_match = cls.CURRENT_RE.search(blob)
        before_match = cls.BEFORE_RE.search(blob)

        current = None
        regular = None

        if current_match:
            try:
                current = float(current_match.group(1).replace(",", ""))
            except ValueError:
                current = None

        if before_match:
            try:
                regular = float(before_match.group(1).replace(",", ""))
            except ValueError:
                regular = None

        if current is None:
            values: list[float] = []
            for raw in cls.MONEY_RE.findall(blob):
                try:
                    value = float(raw.replace(",", ""))
                except ValueError:
                    continue
                if value > 0 and value not in values:
                    values.append(value)

            if values:
                current = values[0]
                if regular is None:
                    regular = max(values)

        if current is not None and regular is None:
            regular = current

        return current, regular

    @classmethod
    def _infer_brand(
        cls,
        product: str | None,
        explicit: str | None = None,
    ) -> str | None:
        if explicit:
            return clean_text(explicit)

        if not product:
            return None

        for brand in cls.BRAND_CANDIDATES:
            if re.search(
                rf"(?<!\w){re.escape(brand)}(?!\w)",
                product,
                re.IGNORECASE,
            ):
                return brand

        return None

    @staticmethod
    def _promotion(text: str | None) -> str | None:
        blob = text or ""
        promos: list[str] = []

        for pattern in (
            r"Rebaja",
            r"Precio en línea",
            r"Ahorra\s*\$\s*[0-9][0-9,]*(?:\.\d{1,2})?",
            r"Combina\s+\d+\s*x\s*\$\s*[0-9][0-9,]*(?:\.\d{1,2})?",
            r"\d+\s*x\s*\$\s*[0-9][0-9,]*(?:\.\d{1,2})?",
        ):
            match = re.search(pattern, blob, flags=re.IGNORECASE)
            if match:
                promos.append(match.group(0))

        return clean_text(" | ".join(dict.fromkeys(promos)))

    def _extract_rows(
        self,
        page,
        category: Category,
        location: Location,
    ) -> list[dict]:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        rows: list[dict] = []
        raw_cards = self._raw_cards(page)

        rejected_missing_sku = 0
        rejected_missing_price = 0

        for card in raw_cards:
            href = clean_text(card.get("href"))
            product = clean_text(card.get("title"))
            text = clean_text(card.get("text")) or ""

            if not href or not product:
                continue

            sku = extract_sku(href)
            if not sku:
                rejected_missing_sku += 1
                continue

            current, regular = self._prices(text)
            if current is None:
                rejected_missing_price += 1
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
                    "brand": self._infer_brand(
                        product,
                        clean_text(card.get("brand")),
                    ),
                    "product": product,
                    "price_current": current,
                    "price_regular": regular,
                    "promotion": self._promotion(text),
                    "pickup_available": None,
                    "store_context_verified": False,
                    "store_context_method": (
                        "bodega_public_catalog_no_store_context"
                    ),
                    "url": urljoin(page.url, href),
                    "price_raw": text,
                }
            )

        self.run_meta.setdefault("card_extraction", []).append(
            {
                "page": page.url,
                "raw_cards": len(raw_cards),
                "rows": len(rows),
                "rejected_missing_sku": rejected_missing_sku,
                "rejected_missing_price": rejected_missing_price,
            }
        )
        return rows

    @staticmethod
    def _target_count(page) -> int | None:
        try:
            text = page.locator("body").inner_text(timeout=5_000)
        except Exception:
            return None

        values: list[int] = []
        patterns = (
            r"\b([0-9][0-9,.]{0,8})\s+productos?\b",
            r"\b([0-9][0-9,.]{0,8})\s+resultados?\b",
            r"\bde\s+([0-9][0-9,.]{0,8})\s+productos?\b",
        )
        for pattern in patterns:
            for raw in re.findall(pattern, text, flags=re.IGNORECASE):
                try:
                    values.append(
                        int(raw.replace(",", "").replace(".", ""))
                    )
                except ValueError:
                    continue

        return max(values) if values else None

    @staticmethod
    def _product_link_count(page) -> int:
        try:
            return page.locator('a[href*="/ip/"]').count()
        except Exception:
            return 0

    def _expand_current_page(
        self,
        page,
        *,
        source_url: str,
        max_rounds: int = 30,
    ) -> dict:
        """Expand lazy-loaded content until the visible product set stabilizes.

        Direct /browse/ catalogue sources never click generic "Ver más"
        controls because sponsored banners use the same label. Those sources
        are handled by the dedicated UI-pagination collector below.
        """
        rounds: list[dict] = []
        stale_rounds = 0
        previous = self._product_link_count(page)

        for round_number in range(1, max_rounds + 1):
            clicked = False
            if "/browse/" not in urlsplit(source_url).path.casefold():
                for label in (
                    "Ver más",
                    "Ver mas",
                    "Mostrar más",
                    "Mostrar mas",
                    "Cargar más",
                    "Cargar mas",
                ):
                    try:
                        button = page.get_by_text(
                            label,
                            exact=False,
                        ).first
                        if button.count() and button.is_visible():
                            button.click(timeout=2_000)
                            clicked = True
                            page.wait_for_timeout(500)
                            break
                    except Exception:
                        continue

            try:
                page.evaluate(
                    "window.scrollTo(0, document.body.scrollHeight)"
                )
            except Exception:
                pass
            page.wait_for_timeout(max(self.wait_ms, 900))

            current = self._product_link_count(page)
            rounds.append(
                {
                    "round": round_number,
                    "product_links": current,
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
            "source_url": source_url,
            "product_links": previous,
            "scroll_rounds": len(rounds),
            "stabilized": stale_rounds >= 3,
            "rounds": rounds,
        }

    @staticmethod
    def _pagination_snapshot(page) -> dict:
        """Find the visible numeric paginator shown below the product grid."""
        try:
            result = page.locator("body").evaluate(
                r"""
                () => {
                  const normalize = value =>
                    String(value || '').replace(/\s+/g, ' ').trim();

                  const visible = el => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return (
                      style.display !== 'none' &&
                      style.visibility !== 'hidden' &&
                      rect.width > 0 &&
                      rect.height > 0
                    );
                  };

                  const controlNumber = el => {
                    const text = normalize(el.innerText || el.textContent);
                    const aria = normalize(el.getAttribute('aria-label'));
                    const title = normalize(el.getAttribute('title'));

                    if (/^\d{1,4}$/.test(text)) {
                      return Number(text);
                    }

                    for (const value of [aria, title]) {
                      const match = value.match(
                        /(?:page|p[aá]gina)\s*(\d{1,4})/i
                      );
                      if (match) return Number(match[1]);
                    }
                    return null;
                  };

                  const candidateSet = new Set();
                  const selectors = [
                    '[aria-label*="pagin" i]',
                    '[aria-label*="page" i]',
                    '[data-testid*="pagin" i]',
                    '[data-automation-id*="pagin" i]',
                    '[class*="pagin" i]',
                    'nav',
                    '[role="navigation"]'
                  ];

                  for (const selector of selectors) {
                    for (const el of document.querySelectorAll(selector)) {
                      if (visible(el)) candidateSet.add(el);
                    }
                  }

                  // Fallback for pagers without semantic container metadata:
                  // walk up from visible numeric controls and score ancestors.
                  for (const control of document.querySelectorAll(
                    'button,a,[role="button"]'
                  )) {
                    if (!visible(control)) continue;
                    if (controlNumber(control) === null) continue;

                    let parent = control.parentElement;
                    for (let depth = 0; depth < 5 && parent; depth++) {
                      if (visible(parent)) candidateSet.add(parent);
                      parent = parent.parentElement;
                    }
                  }

                  let best = null;
                  for (const container of candidateSet) {
                    const controls = Array.from(
                      container.querySelectorAll(
                        'button,a,[role="button"]'
                      )
                    ).filter(visible);

                    const numbers = Array.from(
                      new Set(
                        controls
                          .map(controlNumber)
                          .filter(value => Number.isFinite(value))
                      )
                    ).sort((a, b) => a - b);

                    if (numbers.length < 2) continue;

                    const text = normalize(
                      container.innerText || container.textContent
                    );
                    const currentControl = controls.find(el =>
                      String(el.getAttribute('aria-current') || '')
                        .toLowerCase() === 'page'
                    );
                    const current = currentControl
                      ? controlNumber(currentControl)
                      : null;

                    const hasNext = controls.some(el => {
                      const blob = normalize(
                        [
                          el.innerText,
                          el.textContent,
                          el.getAttribute('aria-label'),
                          el.getAttribute('title'),
                          el.getAttribute('data-testid'),
                          el.getAttribute('data-automation-id')
                        ].join(' ')
                      ).toLowerCase();
                      return (
                        /\b(next|siguiente|pr[oó]xima)\b/i.test(blob) ||
                        /^[>›»→]$/.test(normalize(
                          el.innerText || el.textContent
                        ))
                      );
                    });

                    const score =
                      numbers.length * 20 +
                      (text.includes('…') || text.includes('...') ? 10 : 0) +
                      (hasNext ? 10 : 0) +
                      (current !== null ? 5 : 0);

                    if (!best || score > best.score) {
                      best = {
                        score,
                        numbers,
                        current,
                        max_page: Math.max(...numbers),
                        has_next: hasNext,
                        text: text.slice(0, 500)
                      };
                    }
                  }

                  return best || {
                    score: 0,
                    numbers: [],
                    current: null,
                    max_page: null,
                    has_next: false,
                    text: ''
                  };
                }
                """
            )
        except Exception:
            return {
                "score": 0,
                "numbers": [],
                "current": None,
                "max_page": None,
                "has_next": False,
                "text": "",
            }

        return result or {
            "score": 0,
            "numbers": [],
            "current": None,
            "max_page": None,
            "has_next": False,
            "text": "",
        }

    def _scroll_to_pagination(
        self,
        page,
        *,
        max_scrolls: int = 60,
    ) -> dict:
        """Scroll gradually until the numeric pager becomes visible."""
        snapshot: dict = {}
        last_y = -1

        for round_number in range(1, max_scrolls + 1):
            snapshot = self._pagination_snapshot(page)
            if (
                snapshot.get("max_page")
                and len(snapshot.get("numbers") or []) >= 2
            ):
                snapshot["scroll_rounds"] = round_number - 1
                return snapshot

            try:
                position = page.evaluate(
                    r"""
                    () => {
                      const step = Math.max(
                        700,
                        Math.floor(window.innerHeight * 0.80)
                      );
                      window.scrollBy({top: step, behavior: 'auto'});
                      return {
                        y: Math.floor(window.scrollY),
                        bottom:
                          window.scrollY + window.innerHeight >=
                          document.documentElement.scrollHeight - 8
                      };
                    }
                    """
                )
            except Exception:
                position = {"y": last_y, "bottom": False}

            page.wait_for_timeout(
                min(max(int(self.wait_ms * 0.45), 800), 1_500)
            )

            current_y = int((position or {}).get("y") or 0)
            at_bottom = bool((position or {}).get("bottom"))
            if at_bottom and current_y == last_y:
                break
            last_y = current_y

        snapshot = self._pagination_snapshot(page)
        snapshot["scroll_rounds"] = max_scrolls
        return snapshot

    @staticmethod
    def _click_pagination_next(
        page,
        *,
        expected_page: int,
    ) -> dict:
        """Click the next numbered page (or the pager arrow) inside pager only."""
        try:
            result = page.locator("body").evaluate(
                r"""
                ({expectedPage}) => {
                  const normalize = value =>
                    String(value || '').replace(/\s+/g, ' ').trim();

                  const visible = el => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return (
                      style.display !== 'none' &&
                      style.visibility !== 'hidden' &&
                      rect.width > 0 &&
                      rect.height > 0
                    );
                  };

                  const disabled = el =>
                    el.disabled === true ||
                    el.getAttribute('aria-disabled') === 'true';

                  const controlNumber = el => {
                    const text = normalize(el.innerText || el.textContent);
                    const aria = normalize(el.getAttribute('aria-label'));
                    const title = normalize(el.getAttribute('title'));

                    if (/^\d{1,4}$/.test(text)) return Number(text);
                    for (const value of [aria, title]) {
                      const match = value.match(
                        /(?:page|p[aá]gina)\s*(\d{1,4})/i
                      );
                      if (match) return Number(match[1]);
                    }
                    return null;
                  };

                  const candidates = [];
                  for (const container of document.querySelectorAll(
                    '[aria-label*="pagin" i], [aria-label*="page" i], ' +
                    '[data-testid*="pagin" i], ' +
                    '[data-automation-id*="pagin" i], ' +
                    '[class*="pagin" i], nav, [role="navigation"]'
                  )) {
                    if (!visible(container)) continue;
                    const controls = Array.from(
                      container.querySelectorAll(
                        'button,a,[role="button"]'
                      )
                    ).filter(visible);
                    const nums = controls
                      .map(controlNumber)
                      .filter(value => Number.isFinite(value));
                    if (new Set(nums).size >= 2) {
                      candidates.push({container, controls, nums});
                    }
                  }

                  // Fallback from visible numeric controls.
                  if (!candidates.length) {
                    const numericControls = Array.from(
                      document.querySelectorAll(
                        'button,a,[role="button"]'
                      )
                    ).filter(el =>
                      visible(el) &&
                      Number.isFinite(controlNumber(el))
                    );
                    for (const control of numericControls) {
                      let parent = control.parentElement;
                      for (let depth = 0; depth < 5 && parent; depth++) {
                        const controls = Array.from(
                          parent.querySelectorAll(
                            'button,a,[role="button"]'
                          )
                        ).filter(visible);
                        const nums = controls
                          .map(controlNumber)
                          .filter(value => Number.isFinite(value));
                        if (new Set(nums).size >= 2) {
                          candidates.push({container: parent, controls, nums});
                          break;
                        }
                        parent = parent.parentElement;
                      }
                    }
                  }

                  if (!candidates.length) {
                    return {clicked: false, method: 'pager_not_found'};
                  }

                  candidates.sort(
                    (a, b) =>
                      new Set(b.nums).size - new Set(a.nums).size
                  );
                  const pager = candidates[0];

                  let target = pager.controls.find(
                    el =>
                      !disabled(el) &&
                      controlNumber(el) === expectedPage
                  );
                  let method = 'number';

                  if (!target) {
                    target = pager.controls.find(el => {
                      if (disabled(el)) return false;
                      const blob = normalize(
                        [
                          el.innerText,
                          el.textContent,
                          el.getAttribute('aria-label'),
                          el.getAttribute('title'),
                          el.getAttribute('data-testid'),
                          el.getAttribute('data-automation-id')
                        ].join(' ')
                      ).toLowerCase();
                      return (
                        /\b(next|siguiente|pr[oó]xima)\b/i.test(blob) ||
                        /^[>›»→]$/.test(
                          normalize(el.innerText || el.textContent)
                        )
                      );
                    });
                    method = 'next';
                  }

                  if (!target) {
                    const nonNumeric = pager.controls.filter(
                      el =>
                        !disabled(el) &&
                        controlNumber(el) === null &&
                        !/^(\.\.\.|…)$/.test(
                          normalize(el.innerText || el.textContent)
                        )
                    );
                    target = nonNumeric[nonNumeric.length - 1] || null;
                    method = 'last_non_numeric';
                  }

                  if (!target) {
                    return {clicked: false, method: 'next_not_found'};
                  }

                  target.scrollIntoView({
                    block: 'center',
                    inline: 'center',
                    behavior: 'auto'
                  });
                  target.click();

                  return {
                    clicked: true,
                    method,
                    expected_page: expectedPage,
                    label: normalize(
                      target.getAttribute('aria-label') ||
                      target.innerText ||
                      target.textContent ||
                      target.getAttribute('title')
                    )
                  };
                }
                """,
                {"expectedPage": expected_page},
            )
        except Exception as exc:
            return {
                "clicked": False,
                "method": "exception",
                "error": f"{type(exc).__name__}: {exc}",
            }

        return result or {
            "clicked": False,
            "method": "unknown",
        }

    def _collect_browse_source(
        self,
        page,
        source_url: str,
        category: Category,
        location: Location,
        seen_skus: set[str],
    ) -> tuple[list[dict], dict]:
        """Traverse the actual numeric UI pager one page at a time."""
        self._goto(page, source_url)

        try:
            page.wait_for_selector(
                'a[href*="/ip/"]',
                timeout=25_000,
            )
        except PlaywrightTimeoutError:
            self._save_diagnostics(
                page,
                "no_products_browse_"
                + str(len(self.run_meta.get("sources", [])) + 1),
            )
            return [], {
                "source_url": source_url,
                "actual_url": page.url,
                "rows": 0,
                "new_rows": 0,
                "target_products": self._target_count(page),
                "coverage": 0.0,
                "coverage_threshold": 0.90,
                "product_links": 0,
                "stabilized": False,
                "pagination_complete": False,
                "pages_visited": 0,
                "pagination_method": "ui_numeric",
                "no_product_links": True,
                "explicit_page_meta": [],
            }

        source_target = self._target_count(page)
        pager = self._scroll_to_pagination(page)
        max_page = pager.get("max_page")
        if max_page is not None:
            try:
                max_page = int(max_page)
            except (TypeError, ValueError):
                max_page = None

        if max_page is None or max_page < 1:
            max_page = 1

        max_page = min(max_page, max(1, self.max_pages))
        unique_source: dict[str, dict] = {}
        page_meta: list[dict] = []
        pagination_complete = True

        for page_number in range(1, max_page + 1):
            if page_number > 1:
                click_result = self._click_pagination_next(
                    page,
                    expected_page=page_number,
                )
                if not click_result.get("clicked"):
                    pagination_complete = False
                    page_meta.append(
                        {
                            "page_number": page_number,
                            "click_result": click_result,
                            "rows": 0,
                            "stabilized": False,
                        }
                    )
                    break

                page.wait_for_timeout(self.wait_ms)
                try:
                    page.wait_for_selector(
                        'a[href*="/ip/"]',
                        timeout=20_000,
                    )
                except PlaywrightTimeoutError:
                    pagination_complete = False
                    page_meta.append(
                        {
                            "page_number": page_number,
                            "click_result": click_result,
                            "rows": 0,
                            "stabilized": False,
                            "no_product_links": True,
                        }
                    )
                    break
            else:
                click_result = {
                    "clicked": True,
                    "method": "initial_page",
                }

            pager_snapshot = self._scroll_to_pagination(page)
            rows = self._extract_rows(
                page,
                category,
                location,
            )

            for row in rows:
                sku = clean_text(str(row.get("sku") or ""))
                if sku:
                    unique_source[sku] = row

            page_meta.append(
                {
                    "page_number": page_number,
                    "actual_url": page.url,
                    "rows": len(rows),
                    "unique_source": len(unique_source),
                    "product_links": self._product_link_count(page),
                    "pager_current": pager_snapshot.get("current"),
                    "pager_max_page": pager_snapshot.get("max_page"),
                    "pager_numbers": pager_snapshot.get("numbers"),
                    "pager_text": pager_snapshot.get("text"),
                    "pager_scroll_rounds": pager_snapshot.get(
                        "scroll_rounds"
                    ),
                    "click_result": click_result,
                    "stabilized": True,
                }
            )

            print(
                "Bodega Aurrera UI page "
                f"{page_number}/{max_page}: "
                f"rows={len(rows)}, "
                f"unique_source={len(unique_source)}",
                flush=True,
            )

            # Keep the low-resource/high-latency execution deliberately slow.
            page.wait_for_timeout(self.wait_ms)

        new_rows = [
            row
            for sku, row in unique_source.items()
            if sku not in seen_skus
        ]
        for row in new_rows:
            seen_skus.add(str(row.get("sku")))

        source_coverage = (
            len(unique_source) / source_target
            if source_target not in (None, 0)
            else None
        )

        if (
            pager.get("max_page") not in (None, 0)
            and int(pager.get("max_page")) > self.max_pages
        ):
            pagination_complete = False

        return new_rows, {
            "source_url": source_url,
            "actual_url": page.url,
            "rows": len(unique_source),
            "new_rows": len(new_rows),
            "target_products": source_target,
            "coverage": source_coverage,
            "coverage_threshold": 0.90,
            "product_links": max(
                [
                    int(item.get("product_links") or 0)
                    for item in page_meta
                ]
                or [0]
            ),
            "scroll_rounds": sum(
                int(item.get("pager_scroll_rounds") or 0)
                for item in page_meta
            ),
            "stabilized": bool(page_meta),
            "explicit_pages": max(0, len(page_meta) - 1),
            "explicit_pages_stabilized": pagination_complete,
            "pagination_complete": pagination_complete,
            "pages_visited": len(page_meta),
            "queued_pages_remaining": 0,
            "pagination_method": "ui_numeric_next",
            "pager_max_page": pager.get("max_page"),
            "sponsored_banner_clicks": 0,
            "explicit_page_meta": page_meta,
        }

    @staticmethod
    def _explicit_page_urls(page) -> list[str]:
        try:
            raw = page.locator('a[href*="page="]').evaluate_all(
                r"""
                anchors => Array.from(
                  new Set(
                    anchors
                      .map(a => a.href || '')
                      .filter(Boolean)
                  )
                )
                """
            )
        except Exception:
            return []

        urls: list[str] = []
        for href in raw:
            try:
                query = dict(
                    parse_qsl(
                        urlsplit(href).query,
                        keep_blank_values=True,
                    )
                )
                page_number = int(query.get("page", "0"))
            except (TypeError, ValueError):
                continue
            if page_number > 1 and href not in urls:
                urls.append(href)

        return sorted(
            urls,
            key=lambda href: int(
                dict(
                    parse_qsl(
                        urlsplit(href).query,
                        keep_blank_values=True,
                    )
                ).get("page", "0")
            ),
        )

    @staticmethod
    def _content_child_urls(page, category: Category) -> list[str]:
        """Discover direct content subsections from a parent content category."""
        parent = urlsplit(category.url)
        parent_path = parent.path.rstrip("/")
        if "/content/" not in parent_path:
            return []

        # Parent URLs end in the category id, e.g.
        # /content/cuidado-de-la-ropa/3680083
        # while child sections are siblings below the slug, e.g.
        # /content/cuidado-de-la-ropa/detergente/3680083_3570057
        parts = [part for part in parent_path.split("/") if part]
        if len(parts) < 3:
            return []
        prefix = "/" + "/".join(parts[:-1]) + "/"

        try:
            hrefs = page.locator("a[href]").evaluate_all(
                "anchors => anchors.map(a => a.href || '').filter(Boolean)"
            )
        except Exception:
            return []

        children: list[str] = []
        for href in hrefs:
            try:
                parts = urlsplit(href)
            except Exception:
                continue

            if parts.netloc != parent.netloc:
                continue

            child_path = parts.path.rstrip("/")
            if not child_path.startswith(prefix):
                continue
            if child_path == parent_path:
                continue

            # Direct content subsections have one extra slug plus their id.
            # Keep them as catalogue sources and deduplicate later by SKU.
            clean_url = urlunsplit(
                (parts.scheme, parts.netloc, parts.path, "", "")
            )
            if clean_url not in children:
                children.append(clean_url)

        return children

    def _collect_source(
        self,
        page,
        source_url: str,
        category: Category,
        location: Location,
        seen_skus: set[str],
    ) -> tuple[list[dict], dict]:
        if "/browse/" in urlsplit(source_url).path.casefold():
            return self._collect_browse_source(
                page,
                source_url,
                category,
                location,
                seen_skus,
            )

        """Collect every published page for one direct browse source.

        Pagination URLs are discovered recursively from every visited page.
        The queue is processed sequentially and each navigation observes
        wait_ms, avoiding parallel requests and reducing pressure on the
        existing Chrome session.
        """
        queue: list[str] = [source_url]
        queued: set[str] = {source_url}
        visited_requested: set[str] = set()
        page_meta: list[dict] = []
        unique_source: dict[str, dict] = {}
        all_stabilized = True
        max_page_urls = max(1, self.max_pages)

        while queue and len(visited_requested) < max_page_urls:
            requested_url = queue.pop(0)
            if requested_url in visited_requested:
                continue
            visited_requested.add(requested_url)

            self._goto(page, requested_url)
            page_target = self._target_count(page)

            try:
                page.wait_for_selector(
                    'a[href*="/ip/"]',
                    timeout=25_000,
                )
            except PlaywrightTimeoutError:
                page_meta.append(
                    {
                        "requested_url": requested_url,
                        "actual_url": page.url,
                        "rows": 0,
                        "product_links": 0,
                        "scroll_rounds": 0,
                        "stabilized": False,
                        "no_product_links": True,
                        "target_products": page_target,
                    }
                )
                all_stabilized = False
                continue

            expansion = self._expand_current_page(
                page,
                source_url=requested_url,
            )
            all_stabilized = (
                all_stabilized
                and bool(expansion.get("stabilized"))
            )

            page_rows = self._extract_rows(
                page,
                category,
                location,
            )
            for row in page_rows:
                sku = clean_text(str(row.get("sku") or ""))
                if sku:
                    unique_source[sku] = row

            discovered_pages = self._explicit_page_urls(page)
            for discovered_url in discovered_pages:
                if (
                    discovered_url not in queued
                    and discovered_url not in visited_requested
                    and len(queued) < max_page_urls
                ):
                    queued.add(discovered_url)
                    queue.append(discovered_url)

            page_meta.append(
                {
                    "requested_url": requested_url,
                    "actual_url": page.url,
                    "rows": len(page_rows),
                    "product_links": expansion.get("product_links"),
                    "scroll_rounds": expansion.get("scroll_rounds"),
                    "stabilized": expansion.get("stabilized"),
                    "target_products": page_target,
                    "discovered_page_urls": len(discovered_pages),
                }
            )

            print(
                "Bodega Aurrera page "
                f"{len(visited_requested)}: "
                f"rows={len(page_rows)}, "
                f"unique_source={len(unique_source)}, "
                f"queue={len(queue)}",
                flush=True,
            )

            # Deliberate pacing for the low-resource server and high-latency
            # storefront. _goto() already waits wait_ms after navigation; this
            # additional pause separates catalogue pages.
            page.wait_for_timeout(self.wait_ms)

        new_rows = [
            row
            for sku, row in unique_source.items()
            if sku not in seen_skus
        ]
        for row in new_rows:
            seen_skus.add(str(row.get("sku")))

        pagination_complete = (
            not queue
            and all_stabilized
            and bool(page_meta)
            and not any(
                item.get("no_product_links")
                for item in page_meta
            )
        )

        target_candidates = [
            int(item["target_products"])
            for item in page_meta
            if item.get("target_products") not in (None, 0)
        ]
        source_target = max(target_candidates) if target_candidates else None
        source_coverage = (
            len(unique_source) / source_target
            if source_target not in (None, 0)
            else None
        )

        source_meta = {
            "source_url": source_url,
            "actual_url": page.url,
            "rows": len(unique_source),
            "new_rows": len(new_rows),
            "target_products": source_target,
            "coverage": source_coverage,
            "coverage_threshold": 0.90,
            "product_links": max(
                [
                    int(item.get("product_links") or 0)
                    for item in page_meta
                ]
                or [0]
            ),
            "scroll_rounds": sum(
                int(item.get("scroll_rounds") or 0)
                for item in page_meta
            ),
            "stabilized": all_stabilized,
            "explicit_pages": max(0, len(page_meta) - 1),
            "explicit_pages_stabilized": all_stabilized,
            "pagination_complete": pagination_complete,
            "pages_visited": len(page_meta),
            "queued_pages_remaining": len(queue),
            "explicit_page_meta": page_meta,
        }
        return new_rows, source_meta

    def scrape_category_on_page(
        self,
        page,
        category: Category,
        location: Location,
    ) -> list[dict]:
        """Run Bodega extraction on an already-open browser page.

        The caller owns the browser/context. This method navigates only the
        supplied page and never closes the user's Chrome session.
        """
        self.run_meta = {
            "retailer": "Bodega Aurrera",
            "category_id": category.id,
            "category_url": category.url,
            "blocked_detected": False,
            "manual_verification_required": False,
            "manual_verification_resolved": False,
            "sources": [],
            "browser_session_method": "existing_chrome_cdp",
        }

        explicit_sources = [
            str(url).strip()
            for url in getattr(category, "source_urls", ())
            if str(url).strip()
        ]

        if explicit_sources:
            sources = list(dict.fromkeys(explicit_sources))
            self.run_meta["source_strategy"] = "configured_direct_browse_urls"
            self.run_meta["content_child_urls"] = []
        else:
            # Fallback retained for categories that still use a parent page.
            self._goto(page, category.url)
            child_urls = self._content_child_urls(page, category)
            sources = [category.url] + [
                url for url in child_urls if url != category.url
            ]
            self.run_meta["source_strategy"] = "parent_discovery"
            self.run_meta["content_child_urls"] = child_urls

        self.run_meta["sources_discovered"] = len(sources)
        self.run_meta["configured_source_urls"] = sources

        rows: list[dict] = []
        seen_skus: set[str] = set()

        for source_index, source_url in enumerate(sources, start=1):
            source_rows, source_meta = self._collect_source(
                page,
                source_url,
                category,
                location,
                seen_skus,
            )
            rows.extend(source_rows)
            source_meta["source_index"] = source_index
            self.run_meta["sources"].append(source_meta)

            print(
                f"Bodega Aurrera source={source_index}/{len(sources)}: "
                f"rows={source_meta.get('rows')}, "
                f"new={source_meta.get('new_rows')}, "
                f"cumulative={len(rows)}, "
                f"explicit_pages={source_meta.get('explicit_pages')}, "
                f"stabilized={source_meta.get('stabilized')}",
                flush=True,
            )

        self.run_meta["unique_products"] = len(rows)
        self.run_meta["sku_complete"] = sum(
            bool(row.get("sku")) for row in rows
        )
        self.run_meta["price_complete"] = sum(
            row.get("price_current") is not None for row in rows
        )
        self.run_meta["url_complete"] = sum(
            bool(row.get("url")) for row in rows
        )

        source_targets = [
            int(item["target_products"])
            for item in self.run_meta.get("sources", [])
            if item.get("target_products") not in (None, 0)
        ]
        target_products = sum(source_targets) if source_targets else None
        coverage = (
            len(rows) / target_products
            if target_products not in (None, 0)
            else None
        )
        self.run_meta["target_products"] = target_products
        self.run_meta["coverage"] = coverage
        self.run_meta["coverage_threshold"] = 0.90

        stabilized = all(
            bool(item.get("stabilized"))
            and bool(item.get("explicit_pages_stabilized", True))
            and bool(item.get("pagination_complete"))
            for item in self.run_meta.get("sources", [])
            if not item.get("no_product_links")
        )
        self.run_meta["pages_visited"] = sum(
            int(item.get("pages_visited") or 0)
            for item in self.run_meta.get("sources", [])
        )
        self.run_meta["pagination_verified"] = stabilized
        coverage_ok = bool(
            coverage is not None and coverage >= 0.90
        )
        self.run_meta["coverage_ok"] = coverage_ok
        self.run_meta["status"] = (
            "SUCCESS"
            if rows and (coverage_ok or stabilized)
            else "PARTIAL"
            if rows
            else "EMPTY"
        )

        self._save_diagnostics(
            page,
            f"success_{category.id}",
        )
        return rows

    def scrape_category(
        self,
        category: Category,
        location: Location,
    ) -> list[dict]:
        with sync_playwright() as p:
            launch_kwargs = {"headless": self.headless}
            if self.browser_channel:
                launch_kwargs["channel"] = self.browser_channel

            browser = p.chromium.launch(**launch_kwargs)
            context = browser.new_context(
                locale="es-MX",
                viewport={"width": 1440, "height": 1000},
            )
            page = context.new_page()

            try:
                return self.scrape_category_on_page(
                    page,
                    category,
                    location,
                )
            finally:
                context.close()
                browser.close()


__all__ = [
    "BodegaAurreraBlocked",
    "BodegaAurreraNetworkUnavailable",
    "BodegaAurreraScraper",
]
