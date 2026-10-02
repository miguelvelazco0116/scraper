from __future__ import annotations

import os
import socket
import sys
from urllib.parse import urlparse

import httpx
from openai import APIConnectionError, APIStatusError, OpenAI


HOST = "api.openai.com"
PORT = 443


def _proxy_summary() -> None:
    names = [
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    ]
    active = [name for name in names if os.getenv(name)]
    print("Proxy env :", ", ".join(active) if active else "(ninguno)")


def main() -> int:
    key = os.getenv("OPENAI_API_KEY")
    print("=" * 60)
    print("OpenAI API connectivity diagnostic")
    print("=" * 60)
    print("Python    :", sys.executable)
    print("API key   :", "presente" if key else "AUSENTE")
    _proxy_summary()

    if not key:
        print("ERROR: OPENAI_API_KEY no esta definida.")
        return 2

    try:
        addresses = socket.getaddrinfo(HOST, PORT, type=socket.SOCK_STREAM)
        ips = sorted({item[4][0] for item in addresses})
        print("DNS       : OK ->", ", ".join(ips[:4]))
    except Exception as exc:
        print(f"DNS       : ERROR -> {type(exc).__name__}: {exc}")
        return 3

    try:
        with socket.create_connection((HOST, PORT), timeout=10):
            pass
        print("TCP 443   : OK")
    except Exception as exc:
        print(f"TCP 443   : ERROR -> {type(exc).__name__}: {exc}")
        return 4

    try:
        response = httpx.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=20.0,
            follow_redirects=True,
        )
        print("HTTPS     :", response.status_code)
        if response.status_code == 401:
            print("API       : clave no valida o no aceptada")
            return 5
        if response.status_code >= 400:
            print("API       : respondió HTTP", response.status_code)
            print("Body      :", response.text[:500])
            return 6
        print("API       : OK")
    except Exception as exc:
        print(f"HTTPS     : ERROR -> {type(exc).__name__}: {exc}")
        cause = getattr(exc, "__cause__", None)
        if cause is not None:
            print(f"CAUSE     : {type(cause).__name__}: {cause}")
        return 7

    try:
        client = OpenAI(timeout=30.0, max_retries=0)
        models = client.models.list()
        count = len(getattr(models, "data", []) or [])
        print(f"SDK       : OK ({count} modelos visibles)")
    except APIConnectionError as exc:
        print(f"SDK       : CONNECTION ERROR -> {exc}")
        cause = getattr(exc, "__cause__", None)
        if cause is not None:
            print(f"CAUSE     : {type(cause).__name__}: {cause}")
        return 8
    except APIStatusError as exc:
        print(f"SDK       : HTTP {exc.status_code} -> {exc}")
        return 9
    except Exception as exc:
        print(f"SDK       : ERROR -> {type(exc).__name__}: {exc}")
        return 10

    print("")
    print("OPENAI API CONNECTION: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
