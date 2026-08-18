# Scraper multi-retailer

Base modular para extraer catálogos públicos de retailers de México mediante **Python + Playwright**, generar CSV/Excel y ejecutar pruebas desde GitHub Actions.

## Retailers

| Retailer | Estado | Primera categoría |
|---|---|---|
| Soriana | Implementado | Cuidado bucal |
| Walmart | Pendiente | — |
| Chedraui | Pendiente | — |

La arquitectura está preparada para agregar un módulo por retailer sin mezclar selectores, reglas de navegación o diagnósticos.

## Estructura

```text
scraper/
├── main.py
├── scraper/
│   ├── config.py
│   ├── parsers.py
│   └── retailers/
│       └── soriana.py
├── config/
│   ├── locations.yaml
│   └── soriana/
│       └── categories.yaml
├── tests/
└── .github/workflows/
    └── soriana.yml
```

## Soriana — alcance actual

- Categoría: Cuidado bucal
- URL: `https://www.soriana.com/cuidado-personal-y-belleza/cuidado-bucal/`
- Ubicación lógica inicial: Ciudad de México
- Salida: CSV + Excel
- Navegación: Playwright + Chromium
- Detecta `403`, `GF R01`, `Access Denied` y `Forbidden`
- Guarda screenshot, HTML y registro de `Search-UpdateGrid` en `diagnostics/`
- No incluye CAPTCHA solving, proxies, fingerprint spoofing ni técnicas de evasión.

> `CDMX` es por ahora una etiqueta de corrida. Precio/stock por tienda requiere mapear el selector de ubicación/código postal en una fase posterior.

## Instalación local

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
playwright install chromium
```

## Ejecutar Soriana

```bash
python main.py --retailer soriana --category cuidado-bucal --location cdmx
```

Para abrir el navegador visible:

```bash
python main.py --retailer soriana --category cuidado-bucal --location cdmx --headed
```

## Salida

```text
output/
├── soriana_cuidado_bucal_cdmx.csv
└── soriana_cuidado_bucal_cdmx.xlsx
```

Campos principales: retailer, ubicación, categoría, SKU, marca, producto, precio actual, precio regular, promoción, URL y timestamp.

## GitHub Actions

El workflow `Soriana - Cuidado Bucal` se ejecuta manualmente con **Run workflow** y publica `output/` y `diagnostics/` como artifact de la corrida.

GitHub Actions usa IPs de centros de datos. Si Soriana responde con `GF R01`/403, el scraper guarda diagnósticos y se detiene; no intenta evadir la protección del sitio.

## Agregar otro retailer

1. Crear `scraper/retailers/<retailer>.py`.
2. Crear `config/<retailer>/categories.yaml`.
3. Registrar el retailer en `main.py`.
4. Crear pruebas específicas.
5. Crear su workflow de GitHub Actions si se requiere.

## Pruebas

```bash
pytest -q
```
