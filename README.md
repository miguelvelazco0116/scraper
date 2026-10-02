# Scraper multi-retailer

Proyecto modular para extraer catálogos públicos, precios y promociones de retailers en México y concentrar los resultados en un único Excel.

El proyecto se desarrolla y ejecuta **localmente en Windows**. GitHub se usa para control de versiones, respaldo e integración del código. Los scrapers usan Python, Playwright y/o Selenium según el retailer; cuando un sitio requiere navegador visible se utiliza Google Chrome local.

No se automatizan CAPTCHAs, verificaciones de identidad ni mecanismos para evadir controles de acceso.

## Estado actual

Última actualización: **2 de octubre de 2026**.

Última muestra completa validada localmente:

| Retailer | Categorías activas | Productos validados | Precio actual | SKU/URL | Estado |
|---|---:|---:|---:|---:|---|
| Soriana | 6 | 2,152 | 100% | 100% | Activo |
| Chedraui | 2 | 683 | 100% | 100% | Activo |
| Farmacias del Ahorro | 4 | 288 | 100% | 100% | Activo |
| Farmacias San Pablo | 4 | 232 | 100% | 100% en última validación | Activo |
| Ibarra Mayoreo | 4 | 574 | 100% | 570/574 SKU | Activo |
| Bodega Aurrera | 2 | 555 | 100% | 100% | Activo |
| Farmacias Similares | 2 | 30 | 100% | 100% | Activo |
| La Comer | 2 | Pendiente | Pendiente | Pendiente | Implementado, no activo |
| Walmart | 3 | Pausado | — | — | Pausado |
| Farmacias Guadalajara | 4 | Pausado | — | — | Pausado |

**Total validado en el stack activo: 4,514 filas** considerando la última validación individual de San Pablo (232 productos).

## Retailers activos

El runner principal procesa:

```text
Soriana
Chedraui
Farmacias del Ahorro
Farmacias San Pablo
Ibarra Mayoreo
Bodega Aurrera
Farmacias Similares
```

En pausa:

```text
Walmart
Farmacias Guadalajara
```

La Comer está implementado pero sigue fuera del runner principal hasta completar validación live.

## Ejecución local

Desde PowerShell:

```powershell
cd C:\Proyectos\scraper

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

& .\scripts\run_active_retailers.ps1
```

El wrapper usa el Python activo o detecta el ejecutable local y lanza:

```powershell
python .\scripts\run_all_retailers.py --local-browser
```

El modo `--local-browser` usa Google Chrome visible para los retailers que lo requieren.


### Pacing de Soriana

Soriana ha mostrado bloqueos intermitentes `403 / GF R01` al consultar varias categorías consecutivas. El runner aplica por defecto:

```text
60 s entre categorías consecutivas de Soriana
120 s antes del reintento final
1 reintento al final de la corrida sólo para categorías BLOCKED
```

Los tiempos pueden ajustarse con:

```powershell
python .\scripts\run_all_retailers.py --local-browser --soriana-delay-seconds 60 --soriana-retry-delay-seconds 120
```

No se automatiza ninguna verificación ni se intenta evadir la protección del sitio; si el segundo intento sigue bloqueado, el caso permanece `BLOCKED`.

### Tests generales

```powershell
pytest -q
```

## Output consolidado

La corrida principal genera:

```text
output\concentrado_scraper.xlsx
```

Hojas:

- `Concentrado`
- `Resumen`

Columnas normalizadas:

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
availability_status
is_available
availability_raw
pickup_available
store_context_verified
store_context_method
url
price_raw
```

Los logs del runner completo se guardan en:

```text
diagnostics\run_all
```

## Criterio operativo de calidad

El objetivo principal del proyecto es poder descargar de forma confiable:

- producto;
- precio actual;
- precio regular cuando existe;
- promoción cuando existe;
- disponibilidad;
- categoría y jerarquía;
- timestamp de captura.

Siempre que el retailer lo exponga de forma estable también se conservan:

- SKU;
- URL de producto;
- marca;
- contexto de tienda.

Para la mayoría de retailers se exige cobertura completa de SKU y URL. En **Farmacias San Pablo**, el criterio bloqueante es la cobertura de catálogo y precio/promoción; SKU y URL se consideran campos informativos. En la última validación, sin embargo, ambos quedaron completos en 232/232 productos.

## Disponibilidad de producto

El esquema normaliza la disponibilidad en tres columnas:

```text
availability_status   AVAILABLE | UNAVAILABLE | UNKNOWN
is_available          True | False | vacío
availability_raw      señal original usada para clasificar
```

Reglas:

- `UNAVAILABLE` se asigna sólo cuando existe una señal explícita como stock 0, `outOfStock`, `Agotado`, `Sin existencia` o `No disponible`.
- `AVAILABLE` se asigna cuando el retailer expone una señal positiva como `inStock`, stock mayor que cero o una acción de compra disponible.
- `UNKNOWN` significa que el sitio no expuso una señal suficientemente confiable; no se interpreta como disponible.
- Los productos `UNAVAILABLE` se conservan aunque no tengan `price_current`, para poder medir quiebres de stock y cambios de surtido.
- La falta de precio de un producto `UNAVAILABLE` no degrada por sí sola el estado de calidad del runner.

El resumen del consolidado y `scripts/run_all_retailers.py` reportan:

```text
available_products
unavailable_products
availability_unknown
```

La captura de disponibilidad está habilitada para los siete retailers activos. Las fuentes varían según el retailer: OCC/API cuando existe y señales explícitas del card/PDP cuando el catálogo es visual.

## Soriana

Categorías activas:

```text
cuidado-bucal
limpiadores
detergentes
afeitado-depilacion-dama
desodorantes-para-caballero
desodorantes-para-dama
```

Última muestra completa:

```text
cuidado-bucal                   349
limpiadores                    1077
detergentes                     197
afeitado-depilacion-dama         59
desodorantes-para-caballero     274
desodorantes-para-dama          196
Total                          2152
```

Las rutas de desodorantes fueron validadas el 2 de octubre de 2026.

## Chedraui

Ubicación validada:

```text
id: chedraui-polanco
store: Chedraui Selecto México Polanco
store_id: 232
postal_code: 11500
city: Miguel Hidalgo
state: CDMX
```

Categorías activas:

```text
higiene-bucal   306
lavanderia      377
Total           683
```

La última muestra tuvo cobertura completa de SKU, precio y URL.

## Farmacias del Ahorro

Contexto:

```text
id: fahorro-online
city: Catálogo online
state: Nacional
```

Categorías activas y última muestra:

```text
congestion-nasal      55
preservativos         73
enjuagues-bucales     45
cremas-dentales      115
Total                 288
```

Cobertura completa de SKU, precio actual y URL.

## Farmacias San Pablo

Contexto:

```text
id: san-pablo-online
city: Catálogo online
state: Nacional
```

Categorías activas:

```text
descongestionantes
preservativos
enjuagues-bucales
pastas-dentales
```

La implementación actual usa **Chrome/Selenium para abrir el storefront y descubrir la llamada OCC** que utiliza Farmacias San Pablo. Después consulta desde Python el endpoint público de búsqueda de SAP Commerce para evitar restricciones CORS del navegador.

Fuente detectada:

```text
https://api.farmaciasanpablo.com.mx/rest/v2/fsp/products/search-sponsored
```

La API entrega de forma estructurada:

```text
code
name
url
price
basePrice
potentialPromotions
stock
gtmProperties
pagination
```

Última validación local:

```text
descongestionantes   52 / 52
preservativos        49 / 49
enjuagues-bucales    46 / 46
pastas-dentales      85 / 85
Total               232 / 232
```

Cobertura en la última validación:

```text
Precio actual   232 / 232
SKU             232 / 232
URL             232 / 232
missing_id        0
```

Para probar sólo San Pablo:

```powershell
& .\scripts\run_san_pablo_full.ps1
```

Una categoría específica:

```powershell
& .\scripts\run_san_pablo_full.ps1 -Category enjuagues-bucales
```

Output dedicado:

```text
output\farmacias_san_pablo_test.xlsx
```

## Ibarra Mayoreo

Contexto:

```text
id: ibarra-online
city: Catálogo online
state: Nacional
store: Ibarra Mayoreo online
```

Categorías activas y última muestra:

```text
detergentes-lavatrastes-jab-abarrotes        153
detergentes-lavatrastes-jab-marca-propia      3
dentifricos-abarrotes                         87
perfumeria-abarrotes                         331
Total                                         574
```

Regla crítica: para productos disponibles **sólo se guarda la presentación con precio CAJA**. Un producto marcado explícitamente como `UNAVAILABLE` se conserva aunque no exponga precio CAJA, para poder medir quiebres de stock.

Cobertura de precio de la última validación previa a disponibilidad: 574/574. Cobertura de SKU: 570/574.

Test dedicado:

```powershell
& .\scripts\test_ibarra_mayoreo.ps1
```

## Bodega Aurrera

Contexto:

```text
id: bodega-aurrera-online
city: Catálogo online
state: Nacional
store: Bodega Aurrera online
```

Categorías activas y última muestra:

```text
cuidado-bucal          120
cuidado-de-la-ropa     435
Total                  555
```

Cobertura completa de SKU, precio y URL.

Bodega Aurrera puede solicitar verificación manual durante la navegación. Si aparece, debe completarse manualmente en Chrome y dejar la ventana abierta. El scraper no intenta resolver ni evadir la verificación.

Test dedicado:

```powershell
& .\scripts\test_bodega_aurrera.ps1
```

## Farmacias Similares

Contexto:

```text
id: similares-online
city: Catálogo online
state: Nacional
store: Farmacias Similares online
```

Categorías activas:

```text
aparato-respiratorio   21
condones                9
Total                  30
```

El scraper visita las fichas de producto, obtiene SKU desde `Referencia:`, precio actual, precio regular y promociones.

Última validación: 30/30 productos con SKU, precio y URL.

Test dedicado:

```powershell
& .\scripts\test_farmacias_similares.ps1
```

## La Comer

Implementado pero no activado en el runner principal.

Categorías configuradas:

```text
detergentes-suavizantes
cuidado-bucal
```

Antes de activarlo debe completarse una validación live de catálogo, contexto y precios.

## Retailers en pausa

### Walmart

La implementación se conserva, incluyendo utilidades de sesión y perfil persistente, pero permanece fuera de la corrida activa porque las últimas pruebas mostraron bloqueo de la sesión automatizada.

### Farmacias Guadalajara

Permanece en pausa por problemas de conectividad/bloqueo observados en validaciones anteriores.

## Scripts principales

```text
scripts/
  run_active_retailers.ps1
  run_all_retailers.py
  run_san_pablo_full.ps1
  run_san_pablo_full.py
  test_bodega_aurrera.ps1
  test_farmacias_similares.ps1
  test_ibarra_mayoreo.ps1
  test_la_comer.ps1
  test_san_pablo_chrome.ps1
```

## Estructura

```text
config/
  <retailer>/categories.yaml

scraper/
  retailers/

scripts/
tests/
diagnostics/
output/
main.py
```

Cada retailer mantiene su configuración y extractor separado. `main.py` normaliza todas las salidas y actualiza el concentrado común.

## Historial reciente

### 2 de octubre de 2026

- Se agregó captura normalizada de disponibilidad de producto para los siete retailers activos.
- Se añadieron `availability_status`, `is_available` y `availability_raw` al consolidado.
- Los productos agotados/no disponibles ahora se conservan aunque no tengan precio.
- El runner resume productos disponibles, no disponibles y disponibilidad desconocida.
- Se ejecutó una muestra completa de 24 casos activos.
- Se validaron 2,152 productos en Soriana.
- Se validaron 683 productos en Chedraui.
- Se validaron 288 productos en Farmacias del Ahorro.
- Se validaron 574 productos en Ibarra Mayoreo.
- Se validaron 555 productos en Bodega Aurrera.
- Se validaron 30 productos en Farmacias Similares.
- Farmacias San Pablo fue migrado a la fuente OCC de SAP Commerce.
- San Pablo quedó validado en 232/232 productos, con 232/232 precios, SKU y URL.
- Se agregó control de calidad `COMPLETE/REVIEW` al runner completo.
- Se corrigieron pruebas y utilidades de San Pablo.
- Se eliminó el warning de concatenación all-NA de pandas en el consolidado.
- Se mantuvo la ejecución exclusivamente local en Windows.

### 1 de octubre de 2026

- Se integró Farmacias Similares.
- Se incorporaron Bodega Aurrera, Ibarra Mayoreo y La Comer en la rama de integración.
- Se creó el runner local `run_active_retailers.ps1`.
- Se documentaron criterios comunes de cobertura, paginación, deduplicación y calidad.

## Flujo recomendado

```text
PC local Windows
    ↓
git pull
    ↓
activar venv
    ↓
pytest -q
    ↓
ejecutar scrapers localmente
    ↓
validar output\concentrado_scraper.xlsx
    ↓
revisar diagnostics\
    ↓
git commit / push
```

GitHub no es el entorno de ejecución del scraper; es el repositorio de código y control de versiones.
