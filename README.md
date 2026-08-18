# Scraper multi-retailer

Base modular para extraer catálogos públicos de retailers de México mediante **Python + Playwright**, generar un Excel consolidado y ejecutar pruebas desde GitHub Actions o runners propios.

## Retailers

| Retailer | Estado | Categorías implementadas |
|---|---|---|
| Soriana | Implementado y validado | Cuidado bucal; Cuidado del hogar > Limpiadores; Cuidado del hogar > Limpiadores > Detergentes; Cuidado personal > Afeitado y depilación > Afeitado y depilación para dama |
| Walmart | Implementado; GitHub-hosted bloqueado por desafío de identidad | Belleza y cuidado personal > Higiene y cuidado personal > Cuidado bucal; Limpieza del hogar y cuidado personal > Cuidado de la ropa; Belleza y cuidado personal > Depilación y rasurado |
| Chedraui | Pendiente | — |

La arquitectura usa un módulo por retailer para no mezclar selectores, navegación, paginación o diagnósticos.

## Estructura

```text
scraper/
├── main.py
├── scraper/
│   ├── config.py
│   ├── parsers.py
│   └── retailers/
│       ├── soriana.py
│       ├── walmart.py
│       └── walmart_persistent.py
├── scripts/
│   ├── walmart_prepare_session.py
│   ├── setup_walmart_session.ps1
│   └── run_all_retailers.py
├── config/
│   ├── locations.yaml
│   ├── soriana/categories.yaml
│   └── walmart/categories.yaml
├── tests/
└── .github/
    ├── run/retailer-trigger.txt
    └── workflows/
        ├── soriana.yml
        └── full-self-hosted.yml
```

## Soriana

Categorías configuradas:

- `cuidado-bucal`: Cuidado bucal
- `limpiadores`: Cuidado del hogar > Limpiadores
- `detergentes`: Cuidado del hogar > Limpiadores > Detergentes
- `afeitado-depilacion-dama`: Cuidado personal > Afeitado y depilación > Afeitado y depilación para dama

## Walmart México

Tienda configurada:

```text
id: sc-toreo
store: SC Toreo
store_id: 2344
postal_code: 11220
city: Miguel Hidalgo
state: CDMX
```

Categorías configuradas:

- `cuidado-bucal`
- `cuidado-de-la-ropa`
- `depilacion-y-rasurado`

El módulo de Walmart recorre paginación, deduplica SKU/URL, valida el contexto de tienda y no intenta resolver CAPTCHAs ni desafíos de identidad.

## Solución operativa para Walmart

Las pruebas en GitHub-hosted muestran `Verifica tu identidad / Mantén presionado` antes del catálogo. Por eso Walmart debe ejecutarse en un **runner self-hosted de Windows con sesión interactiva y perfil persistente de Chromium**.

El perfil de Walmart debe vivir **fuera del repositorio**, porque `actions/checkout` limpia el workspace. La ruta predeterminada es:

```text
C:\scraper_profiles\walmart_sc_toreo
```

### 1. Preparar la sesión una sola vez

Desde PowerShell, dentro del repositorio:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_walmart_session.ps1
```

Se abrirá Walmart en Chromium. Completa manualmente cualquier verificación y confirma **SC Toreo / CP 11220**. El script guarda la sesión en el perfil persistente. No subas ese directorio a GitHub.

### 2. Configurar el runner self-hosted

Agrega esta computadora Windows como runner self-hosted del repositorio con las etiquetas estándar:

```text
self-hosted
windows
x64
```

Para Walmart, inicia el runner **interactivamente en una sesión de Windows abierta**. No lo ejecutes como servicio si necesitas navegador visible.

Opcionalmente crea la variable de repositorio `WALMART_PROFILE_DIR` si quieres usar una ruta distinta a `C:\scraper_profiles\walmart_sc_toreo`.

### 3. Ejecutar todos los retailers y categorías

En GitHub Actions selecciona:

```text
Full Scraper - Self Hosted
```

y ejecuta **Run workflow**.

El workflow usa el mismo equipo para:

1. Soriana: todas las categorías configuradas.
2. Walmart: todas las categorías configuradas con SC Toreo y el perfil persistente.
3. Validación de SKU, precios, URL, duplicados y contexto de tienda.
4. Un único artifact con:

```text
output/concentrado_scraper.xlsx
```

El workbook tiene:

- `Concentrado`: todos los productos extraídos.
- `Resumen`: estado y métricas de cada retailer/categoría.

Si Walmart vuelve a pedir verificación, la corrida se detiene de forma controlada. Vuelve a ejecutar `setup_walmart_session.ps1` manualmente para renovar la sesión; no se automatiza el desafío.

## Ejecución individual con perfil persistente

```powershell
python main.py --retailer walmart --category cuidado-bucal --store sc-toreo --profile-dir "C:\scraper_profiles\walmart_sc_toreo" --headed
python main.py --retailer walmart --category cuidado-de-la-ropa --store sc-toreo --profile-dir "C:\scraper_profiles\walmart_sc_toreo" --headed
python main.py --retailer walmart --category depilacion-y-rasurado --store sc-toreo --profile-dir "C:\scraper_profiles\walmart_sc_toreo" --headed
```

## Campos de salida

```text
scrape_timestamp
retailer
city
state
postal_code
store
store_id
department
category
subcategory
sub_subcategory
category_id
sku
brand
product
price_current
price_regular
promotion
pickup_available
store_context_verified
store_context_method
url
price_raw
```

## Instalación local

```bash
python -m venv .venv
pip install -r requirements.txt
playwright install chromium
```

## Pruebas

```bash
pytest -q
```
