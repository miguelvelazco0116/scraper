# Scraper multi-retailer

Proyecto modular para extraer catálogos públicos, precios y promociones de retailers en México y concentrar los resultados en un único Excel.

El proyecto se desarrolla y ejecuta **localmente en Windows**. GitHub se usa para control de versiones, respaldo e integración del código. Los scrapers usan Python, Playwright y/o Selenium según el retailer; cuando un sitio requiere navegador visible se utiliza Google Chrome local.

No se automatizan CAPTCHAs, verificaciones de identidad ni mecanismos para evadir controles de acceso.

## Estado actual

Última actualización: **6 de octubre de 2026**.

Última muestra completa validada localmente:

| Retailer | Categorías activas | Productos validados | Precio actual | SKU/URL | Estado |
|---|---:|---:|---:|---:|---|
| Soriana | 6 | 2,152 | 100% | 100% | Activo |
| Chedraui | 2 | 655 live | 100% | 100% | Activo |
| Farmacias del Ahorro | 4 | 288 | 100% | 100% | Activo |
| Farmacias San Pablo | 4 | 232 | 100% | 100% en última validación | Activo |
| Ibarra Mayoreo | 4 | 570/571 live | 100% en filas capturadas | URL 100%; SKU informativo | Activo / Scrapy |
| Bodega Aurrera | 2 | 555 | 100% | 100% | Activo |
| Farmacias Similares | 2 | 30 | 100% | 100% | Activo |
| La Comer | 2 | Pendiente | Pendiente | Pendiente | Implementado, no activo |
| Walmart | 3 | Pausado | — | — | Pausado |
| Farmacias Guadalajara | 4 | Pausado | — | — | Pausado |

**Total observado en la última base validada del stack activo: 4,510 filas**, considerando Ibarra Mayoreo en 570/571 productos live y la última validación individual de San Pablo (232 productos).

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

## Integración Scrapy

El proyecto incorpora ahora Scrapy como capa de crawling para retailers que pueden
consultarse de forma estable por HTTP/API. Scrapy no sustituye los flujos asistidos
de Chrome/CDP, Playwright o Selenium cuando el retailer requiere sesión visible o
protecciones que no deben evadirse.

Arquitectura híbrida:

```text
Scrapy              -> HTTP/API cuando el acceso directo es estable
Playwright/Selenium -> browser persistente cuando el retailer exige sesión
Raw CDP             -> sesiones manual-asistidas ya validadas
quality gate        -> impide reemplazar una muestra buena con una parcial
atomic + lock       -> protege Excel/JSON y serializa el master
scraper/*           -> parsers, normalización, disponibilidad y esquema canónico
```

La política es **capability-first**: un retailer no se migra a Scrapy por
uniformidad. Si el acceso HTTP directo devuelve bloqueos pero el storefront
normal funciona con una sesión legítima de navegador, se mantiene el motor de
browser y se persiste el perfil local.

Versión integrada:

```text
Scrapy 2.19.0
Python 3.10+
```

El primer retailer migrado y validado completamente en Scrapy es
**Farmacias del Ahorro**. Reutiliza el parser y las reglas existentes de Empathy
Search, precio, promoción y disponibilidad.

Validación Scrapy del 6 de octubre de 2026:

```text
congestion-nasal       55 / 55   COMPLETE
preservativos          73 / 73   COMPLETE
enjuagues-bucales      45 / 45   COMPLETE
cremas-dentales       115 / 115  COMPLETE
Total                 288 / 288
```

El runner principal usa Scrapy para Farmacias del Ahorro y sólo actualiza el
consolidado cuando la categoría termina `COMPLETE`.

**Ibarra Mayoreo** es el segundo retailer migrado al runner principal de
Scrapy. Para este retailer se acepta una muestra con cobertura **>=99%** como
`SAMPLE_ACCEPTED`, siempre que las filas que requieren precio tengan precio
completo y la corrida no reporte PDP fallidos, productos disponibles sin precio
CAJA ni errores de parsing. Se sigue intentando 100% y se conserva el target
publicado para medir el gap real.

Validación Scrapy live del 6 de octubre de 2026:

```text
detergentes-lavatrastes-jab-abarrotes       152 / 152  COMPLETE
detergentes-lavatrastes-jab-marca-propia      2 / 2    COMPLETE
dentifricos-abarrotes                         86 / 86   COMPLETE
perfumeria-abarrotes                         330 / 331  SAMPLE_ACCEPTED
Total                                        570 / 571
```

El runner principal usa Scrapy tanto para Farmacias del Ahorro como para Ibarra
Mayoreo.

Instalación/actualización del ambiente:

```powershell
cd C:\Proyectos\scraper
C:\Proyectos\venvs\scraper\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Prueba Scrapy sin modificar el consolidado:

```powershell
& .\scripts\run_scrapy.ps1 `
    -Retailer farmacias-del-ahorro `
    -Category congestion-nasal
```

Salida dedicada:

```text
output\scrapy\farmacias_del_ahorro_congestion-nasal.xlsx
```

Sólo después de obtener estado `COMPLETE` puede actualizarse el consolidado:

```powershell
& .\scripts\run_scrapy.ps1 `
    -Retailer farmacias-del-ahorro `
    -Category congestion-nasal `
    -UpdateConsolidated
```

El pipeline Scrapy escribe las mismas columnas canónicas que `main.py`.
Actualiza `output\concentrado_scraper.xlsx` cuando la muestra termina
`COMPLETE` o, para retailers con umbral explícito como Ibarra Mayoreo,
`SAMPLE_ACCEPTED`. Los productos explícitamente `UNAVAILABLE` pueden carecer
de precio sin degradar por sí solos la calidad de la muestra.

### Guardas de robustez

La capa común aplica las siguientes protecciones:

```text
escritura de Excel/JSON -> temporal + reemplazo atómico
master Excel            -> lock entre procesos en read-modify-write
HTTP 401/403/429        -> clasificación explícita de bloqueo
target + coverage       -> se conservan también en el resumen maestro
AVAILABLE/UNKNOWN       -> precio obligatorio
UNAVAILABLE             -> puede conservarse sin precio
SKU/URL                 -> obligatorios para productos no agotados,
                           salvo excepciones declaradas por retailer
diagnostics/scrapy      -> target, páginas, gaps, errores y faltantes
```

Una interrupción durante la escritura no reemplaza el último archivo válido.
El runner principal tampoco convierte un `SAMPLE_ACCEPTED` en `COMPLETE`;
conserva el target y la cobertura exacta.

Los motores legacy pasan por un **quality gate** antes de actualizar el master.
Una corrida con `PARTIAL`, cobertura menor al target, precio requerido faltante,
nombre vacío, `price_regular < price_current` o contexto de tienda inválido
termina con código controlado y conserva la última muestra válida.

Validación secuencial de regresiones y candidatos Scrapy, sin modificar el
consolidado:

```powershell
& .\scripts\validate_scrapy_candidates.ps1 -Group all
```

Grupos disponibles:

```text
regression  -> Farmacias del Ahorro + Ibarra
candidates  -> Farmacias Similares + Farmacias San Pablo
all         -> ambos grupos, en secuencia
```

Los logs quedan en `diagnostics\scrapy_validation\` y el resumen en
`diagnostics\scrapy_validation\summary.csv`.

Health check estructural sin scraping:

```powershell
& .\scripts\project_healthcheck.ps1
```

Valida master, esquema canónico, disponibilidad, precios, duplicados, política
de motores, perfiles persistentes y configuración de categorías. El resultado
también se guarda en `diagnostics\project_healthcheck.json`.

Validación integral del proyecto sin tocar el consolidado:

```powershell
& .\scripts\validate_project.ps1
```

Incluyendo los retailers que requieren navegador:

```powershell
& .\scripts\validate_project.ps1 -IncludeBrowser
```

La validación integral ejecuta `pytest`, healthcheck, regresión Scrapy y, con
`-IncludeBrowser`, Farmacias Similares + Farmacias San Pablo usando perfiles
persistentes.

La política central de motores vive en:

```text
config/engine_policy.yaml
```

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


### Preparación / recuperación de perfiles persistentes

Si Soriana empieza una corrida ya bloqueado o Chedraui pierde el contexto de Polanco, se puede preparar el perfil local una sola vez antes de reintentar:

```powershell
& .\scripts\prepare_retailer_profiles.ps1 -Retailer soriana
& .\scripts\prepare_retailer_profiles.ps1 -Retailer chedraui
```

También puede abrir ambos secuencialmente:

```powershell
& .\scripts\prepare_retailer_profiles.ps1 -Retailer all
```

La utilidad abre Chrome con el mismo perfil dedicado que usa el scraper. No resuelve verificaciones automáticamente: permite completar manualmente cualquier validación normal del sitio o confirmar Polanco y después conserva cookies/localStorage/sessionStorage para las siguientes corridas. No debe ejecutarse al mismo tiempo que el scraper porque Chrome bloquea el perfil mientras está abierto.


### Pacing de Soriana

Soriana ha mostrado bloqueos intermitentes `403 / GF R01` al consultar varias categorías consecutivas. El runner aplica por defecto:

```text
120 s entre categorías consecutivas exitosas dentro de cada tanda
tandas de 2 categorías intercaladas con otros retailers
si una categoría queda BLOCKED, la siguiente de esa tanda se difiere
600 s antes del reintento final
1 reintento al final para categorías BLOCKED o DEFERRED
300 s mínimos antes del siguiente retry si un retry vuelve a quedar BLOCKED
perfil persistente .soriana_profile
```

Los tiempos pueden ajustarse con:

```powershell
python .\scripts\run_all_retailers.py --local-browser --soriana-delay-seconds 120 --soriana-retry-delay-seconds 600
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


El runner **no elimina el consolidado al comenzar**. Una categoría exitosa reemplaza sólo su muestra. Si la extracción actual falla pero existe una captura previa, el resumen la conserva con:

```text
data_status = STALE_RETAINED
quality_status = STALE
```

Si la captura actual fue exitosa:

```text
data_status = FRESH
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

- `UNAVAILABLE` se asigna sólo cuando existe una señal explícita confiable como stock 0, `outOfStock`, `Agotado`, `Sin existencia` o `Sin stock`. En Soriana/Chedraui una frase genérica `No disponible` no basta porque puede describir un canal de entrega y no el stock total.
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


### Sesión persistente Soriana

El runner local utiliza `.soriana_profile` para conservar cookies y storage entre categorías y entre corridas. La primera sesión puede hacer un warm-up del homepage antes de entrar a la categoría; si el perfil ya contiene cookies vigentes se evita esa carga adicional.

Las seis categorías se distribuyen en tandas de dos y se intercalan con otros retailers. La paginación sigue usando la navegación del propio storefront, que dispara `Search-UpdateGrid` / `Search-ShowAjax`, evitando cargas completas innecesarias de nuevas páginas.

Si una categoría recibe `403 / GF R01`, no se reintenta inmediatamente. Queda pendiente para un único intento al final de la corrida después del cooldown configurado.


El bloqueo también se guarda localmente en `.soriana_profile/soriana_circuit.json`. Si se inicia otra corrida antes de que expire el cooldown, Soriana responde localmente como `DEFERRED` sin hacer un nuevo request al retailer.

Chequeo previo opcional del perfil:

```powershell
& .\scripts\prepare_soriana_session.ps1
```

Resultados:

```text
SORIANA_SESSION_READY  -> homepage accesible; circuito limpio
DEFERRED               -> cooldown local todavía activo
BLOCKED                -> homepage sigue devolviendo bloqueo; se reinicia el cooldown
```

El runner completo ejecuta cada tanda Soriana en **una sola sesión persistente de Chrome**, en lugar de cerrar/abrir navegador por categoría.

## Modo manual-asistido de Soriana

Cuando el acceso directo automatizado a una categoría recibe `403 / GF R01`, puede usarse un flujo donde la navegación inicial queda completamente bajo control del usuario y el scraper sólo extrae la pestaña ya abierta.

1. Abrir un Chrome dedicado con debugging local:

```powershell
& .\scripts\open_soriana_manual_chrome.ps1
```

2. En ese Chrome, navegar manualmente hasta la categoría deseada y esperar a que aparezca el grid de productos.

3. Sin cerrar Chrome, ejecutar en otra consola:

```powershell
& .\scripts\scrape_soriana_manual_session.ps1 -Category limpiadores
```

El script:
- se conecta por CDP a `127.0.0.1:9222`;
- no hace `page.goto()` a la categoría;
- valida que la pestaña abierta coincida con la categoría configurada;
- extrae el grid y automatiza la paginación existente;
- escribe `output\soriana_manual_session.xlsx`;
- no modifica el consolidado salvo que se use `-UpdateConsolidated`.

Para actualizar el consolidado después de validar la captura:

```powershell
& .\scripts\scrape_soriana_manual_session.ps1 -Category limpiadores -UpdateConsolidated
```

El perfil manual se guarda en `.soriana_manual_profile` y está excluido de Git.

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
higiene-bucal   303 / 303
lavanderia      352 / 352
Total           655 / 655
```

La validación live del 8 de octubre de 2026 tuvo cobertura completa de catálogo,
SKU y URL en ambas categorías. Los productos que requerían precio quedaron
completos: Higiene Bucal 297/297 y Lavandería 338/338. No hubo gaps de
paginación, páginas VTEX estructuradas faltantes ni errores
`price_regular < price_current`.


### Contexto persistente Chedraui

Chedraui utiliza `.chedraui_profile` y la implementación activa `chedraui_polanco_api.py`.

Antes de extraer catálogo:

1. abre el origen de Chedraui para recuperar/crear la sesión VTEX;
2. consulta `/api/sessions`;
3. prepara `country=MEX` y `postalCode=11500` cuando existe `sessionToken`;
4. consulta `/api/checkout/pub/pickup-points` y busca candidatos de Polanco;
5. reutiliza la tienda persistida si el estado del navegador ya la verifica;
6. consulta el orderForm actual y valida store/pickup cuando el estado lo expone;
7. utiliza el selector visual de tienda como fallback cuando todavía hace falta confirmar Polanco.

El catálogo/precio autoritativo sigue siendo `productSearchV3`. La disponibilidad de Chedraui se obtiene de `AvailableQuantity`: mayor a cero = `AVAILABLE`, cero = `UNAVAILABLE`, ausente = `UNKNOWN`.

## Farmacias del Ahorro

Motor activo: **Scrapy 2.19.0**.

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

La validación Scrapy del 6 de octubre de 2026 reprodujo 288/288 productos con
`coverage=1.0`, `quality=COMPLETE` y precio completo en las cuatro categorías.

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

La implementación activa usa **Chrome/Selenium para abrir el storefront y descubrir la llamada OCC** que utiliza Farmacias San Pablo. Después consulta desde Python el endpoint público de búsqueda de SAP Commerce para evitar restricciones CORS del navegador.

Se implementó además un spider Scrapy/OCC candidato. La validación live del
7 de octubre de 2026 confirmó que el endpoint directo requiere el contexto de
sesión del navegador. Por robustez, **San Pablo permanece en modo
manual-asistido**: el usuario abre Chrome manualmente y el scraper se conecta
a esa sesión en `127.0.0.1:9223`. Después consume OCC estructurado. El
scraper no abre ni cierra el Chrome manual.

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

Para probar sólo San Pablo, primero abre el Chrome manual dedicado:

```powershell
& .\scripts\open_san_pablo_manual_chrome.ps1
```

En ese Chrome, navega manualmente a Farmacias San Pablo y confirma que el sitio
carga. Déjalo abierto. En otra consola ejecuta:

```powershell
& .\scripts\run_san_pablo_full.ps1
```

El scraper se adjunta a `127.0.0.1:9223` y no cierra el navegador al terminar.

Una categoría específica:

```powershell
& .\scripts\run_san_pablo_full.ps1 -Category enjuagues-bucales
```

Output dedicado:

```text
output\farmacias_san_pablo_test.xlsx
```

Prueba del candidato Scrapy/OCC sin modificar el consolidado:

```powershell
& .\scripts\run_scrapy.ps1 `
    -Retailer farmacias-san-pablo `
    -Category enjuagues-bucales
```

El candidato usa `search-sponsored`, paginación OCC y reutiliza el parser
canónico de precio, promoción, stock y SKU. SKU/URL continúan siendo métricas
informativas para el criterio de San Pablo.

## Ibarra Mayoreo

Contexto:

```text
id: ibarra-online
city: Catálogo online
state: Nacional
store: Ibarra Mayoreo online
```

Motor activo: **Scrapy 2.19.0**.

Categorías activas y última validación live:

```text
detergentes-lavatrastes-jab-abarrotes       152 / 152  COMPLETE
detergentes-lavatrastes-jab-marca-propia      2 / 2    COMPLETE
dentifricos-abarrotes                         86 / 86   COMPLETE
perfumeria-abarrotes                         330 / 331  SAMPLE_ACCEPTED
Total                                        570 / 571
```

Regla crítica: para productos disponibles **sólo se guarda la presentación con precio CAJA**. Un producto marcado explícitamente como `UNAVAILABLE` se conserva aunque no exponga precio CAJA, para poder medir quiebres de stock.

Criterio Scrapy de cobertura para Ibarra:

```text
100%            COMPLETE
>=99% y <100%   SAMPLE_ACCEPTED
<99%            PARTIAL
```

`SAMPLE_ACCEPTED` no se presenta como censo completo: el output conserva el
target publicado y la cobertura exacta. Puede actualizar el consolidado si la
corrida no tiene errores de PDP/parsing ni productos disponibles sin precio
CAJA.

La muestra Scrapy actual conserva 570 filas observadas sobre un target live de
571. Perfumería mantiene documentado un gap de 1 producto publicado no
observable en el storefront; no se fabrica una fila para completarlo.

Test dedicado del motor anterior (fallback/diagnóstico):

```powershell
& .\scripts\test_ibarra_mayoreo.ps1
```

Prueba dedicada con Scrapy, sin tocar el consolidado:

```powershell
& .\scripts\run_scrapy.ps1 `
    -Retailer ibarra-mayoreo `
    -Category dentifricos-abarrotes
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

Bodega Aurrera puede solicitar verificación manual durante la navegación. Si aparece,
debe completarse manualmente en Chrome. El motor activo usa el perfil persistente
`.bodega_aurrera_profile`, por lo que cookies/estado pueden reutilizarse en
corridas posteriores. El scraper no intenta resolver ni evadir la verificación.

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

El motor activo visita las fichas de producto, obtiene SKU desde
`Referencia:`, precio actual, precio regular y promociones.

Se implementó un spider Scrapy candidato que combina tarjeta de categoría y PDP.
La validación live del 7 de octubre de 2026 mostró que el storefront público sí
expone el catálogo, pero la sesión HTTP directa de Scrapy desde el servidor es
bloqueada antes de descubrir productos. Por robustez, **Farmacias Similares se
mantiene en Playwright con perfil persistente `.similares_profile`**.

El perfil conserva cookies y una verificación manual legítimamente resuelta entre
corridas. El scraper no automatiza ni evade CAPTCHAs.

En la validación del 2 de octubre de 2026, la categoría
`aparato-respiratorio` declaró 21 productos pero sólo expuso 13 productos
observables (8 en la primera página y 5 en la segunda). El scraper no inventa
las filas faltantes ni las clasifica automáticamente como agotadas; registra el
**gap de catálogo** mediante target, observados y faltantes. El motor activo no
se sustituirá hasta que el candidato Scrapy sea validado live.

Test dedicado del motor actual:

```powershell
& .\scripts\test_farmacias_similares.ps1
```

Prueba del candidato Scrapy sin modificar el consolidado:

```powershell
& .\scripts\run_scrapy.ps1 `
    -Retailer farmacias-similares `
    -Category condones
```

Después se prueba:

```powershell
& .\scripts\run_scrapy.ps1 `
    -Retailer farmacias-similares `
    -Category aparato-respiratorio
```

## La Comer

Implementado pero no activado en el runner principal. La implementación ya soporta
perfil persistente `.la_comer_profile` para que la validación live futura
reutilice el contexto de sesión.

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

- Se agregaron perfiles persistentes para Soriana y Chedraui.
- Soriana se distribuye en tandas de dos categorías intercaladas con otros retailers; tras un BLOCKED difiere el resto de la tanda y usa cooldown final de 600 s.
- Chedraui prepara región VTEX por CP 11500, consulta pickup points y verifica orderForm antes del fallback visual.
- Se agregó una utilidad manual de preparación/recuperación de perfiles persistentes.
- Chedraui obtiene disponibilidad estructurada desde `productSearchV3.AvailableQuantity`.
- El consolidado conserva la última muestra válida como `STALE_RETAINED` cuando una categoría falla.
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
