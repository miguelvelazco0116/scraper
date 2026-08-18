# Scraper multi-retailer

Base modular para extraer catálogos públicos de retailers de México mediante **Python + Playwright**, generar CSV/Excel y ejecutar pruebas desde GitHub Actions.

## Retailers

| Retailer | Estado | Categorías implementadas |
|---|---|---|
| Soriana | Implementado | Cuidado bucal; Cuidado del hogar > Limpiadores; Cuidado del hogar > Limpiadores > Detergentes |
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

Categorías configuradas:

- `cuidado-bucal`: Cuidado bucal — `https://www.soriana.com/cuidado-personal-y-belleza/cuidado-bucal/`
- `limpiadores`: Cuidado del hogar > Limpiadores — `https://www.soriana.com/limpieza-del-hogar/limpiadores/`
- `detergentes`: Cuidado del hogar > Limpiadores > Detergentes — `https://www.soriana.com/limpieza-del-hogar/limpiadores/detergentes/`

La salida conserva la jerarquía en columnas separadas:

```text
category | subcategory | sub_subcategory
```

Características:

- Ubicación lógica inicial: Ciudad de México
- Salida: CSV + Excel
- Navegación: Playwright + Chromium
- Recorre la paginación completa reportada por Soriana
- Detecta `403`, `GF R01`, `Access Denied` y `Forbidden`
- Guarda screenshot, HTML y registro de `Search-UpdateGrid` / `Search-ShowAjax` en `diagnostics/`
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

Cuidado bucal:

```bash
python main.py --retailer soriana --category cuidado-bucal --location cdmx
```

Limpiadores:

```bash
python main.py --retailer soriana --category limpiadores --location cdmx
```

Detergentes:

```bash
python main.py --retailer soriana --category detergentes --location cdmx
```

Para abrir el navegador visible agrega `--headed`.

## Salida

Ejemplos:

```text
output/
├── soriana_cuidado_bucal_cdmx.xlsx
├── soriana_limpiadores_cdmx.xlsx
└── soriana_detergentes_cdmx.xlsx
```

Campos principales: retailer, ubicación, category, subcategory, sub_subcategory, SKU, marca, producto, precio actual, precio regular, promoción, URL y timestamp.

## GitHub Actions

El workflow `Soriana Scraper` permite elegir `cuidado-bucal`, `limpiadores` o `detergentes` desde **Run workflow** y también puede leer `category` y `location` desde `.github/run/soriana-trigger.txt` para corridas disparadas desde ChatGPT.

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
