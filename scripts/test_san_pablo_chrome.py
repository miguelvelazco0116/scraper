from __future__ import annotations

import argparse
import json
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


URLS = {
    "descongestionantes": "https://www.farmaciasanpablo.com.mx/medicamentos/gripe-y-tos/descongestionantes/c/060070004",
    "preservativos": "https://www.farmaciasanpablo.com.mx/salud-sexual/bienestar-sexual/preservativos/",
    "enjuagues-bucales": "https://www.farmaciasanpablo.com.mx/cuidado-personal-y-belleza/cuidado-bucal/enjuagues-bucales/c/030040003",
    "pastas-dentales": "https://www.farmaciasanpablo.com.mx/salud-natural/salud-natural/tes/c/Pastas-dentales0/c/030040007",
}

DIAGNOSTICS = Path("diagnostics")


def blocked(text: str, title: str) -> bool:
    blob = f"{title}\n{text}".casefold()
    markers = [
        "access denied",
        "you don't have permission to access",
        "request rejected",
        "verify you are human",
        "verifica que eres humano",
        "captcha",
    ]
    return any(marker in blob for marker in markers)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", choices=sorted(URLS), default="enjuagues-bucales")
    args = parser.parse_args()

    options = webdriver.ChromeOptions()
    options.add_argument("--lang=es-MX")
    options.add_argument("--window-size=1440,1000")

    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(60)

    DIAGNOSTICS.mkdir(parents=True, exist_ok=True)
    out = DIAGNOSTICS / f"san_pablo_{args.category}_chrome_test.json"

    try:
        url = URLS[args.category]
        print(f"Abriendo Chrome: {url}")

        try:
            driver.get(url)
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
        except (TimeoutException, WebDriverException) as exc:
            payload = {
                "category": args.category,
                "url": url,
                "status": "NETWORK_OR_BROWSER_ERROR",
                "error": f"{type(exc).__name__}: {exc}",
            }
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 3

        title = driver.title or ""
        body = driver.find_element(By.TAG_NAME, "body").text or ""
        is_blocked = blocked(body, title)

        product_links = driver.execute_script(
            """
            return Array.from(document.querySelectorAll('a[href*="/p/"]'))
              .map(a => a.href)
              .filter(Boolean).length;
            """
        ) or 0

        payload = {
            "category": args.category,
            "url": url,
            "title": title,
            "blocked": is_blocked,
            "product_links": int(product_links),
            "body_preview": " ".join(body.split())[:500],
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        try:
            driver.save_screenshot(
                str(DIAGNOSTICS / f"san_pablo_{args.category}_chrome_test.png")
            )
        except Exception:
            pass

        print(json.dumps(payload, ensure_ascii=False, indent=2))

        if is_blocked:
            print("RESULTADO: BLOCKED")
            return 2

        if int(product_links) == 0:
            print("RESULTADO: ACCESO OK, pero no detectamos links /p/ todavía")
            return 4

        print("RESULTADO: ACCESO OK")
        return 0
    finally:
        driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
