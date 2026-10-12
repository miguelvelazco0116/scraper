# Scraper

> **Registro de métodos validados:** antes de cambiar la estrategia de un retailer, revisar `docs/validated_scraping_patterns.md`. Los métodos sólo se marcan como VALIDADO después de una prueba real exitosa. multi-retailer

Proyecto modular para extraer catálogos públicos, precios y promociones de retailers en México y concentrar los resultados en un único Excel.

El proyecto se desarrolla y ejecuta **localmente en Windows**. GitHub se usa para control de versiones, respaldo e integración del código. Los scrapers usan Python, Playwright y/o Selenium según el retailer; cuando un sitio requiere navegador visible se utiliza Google Chrome local.

No se automatizan CAPTCHAs, verificaciones de identidad ni mecanismos para evadir controles de acceso.

## Estado actual

Última actualización: **12 de octubre de 2026**.

Última muestra completa validada localmente:

| Retailer | Categorías activas | Productos validados | Precio actual | SKU/URL | Estado |
|---|---:|---:|---:|---:|---|
| Soriana | 6 | 2,148 | 100% | 100% | Validado / Fase 2 |
| Chedraui | 2 | 667 | 100% | 100% | Validado / Fase 1 |
| Farmacias del Ahorro | 4 | 288 | 100% | 100% | Validado / Fase 1 |
| Farmacias San Pablo | 4 | 232 | 100% | 100% en última validación | Activo |
| Ibarra Mayoreo | 4 | 572 | 100% | SKU informativo; gates unitarios completos | Validado / Fase 1 |
| Bodega Aurrera | 2 | 555 | 100% | 100% | Activo |
| Farmacias Similares | 2 | 30 | 100% | 100% | Activo |
| La Comer | 2 | Pendiente | Pendiente | Pendiente | Implementado, no activo |
| Walmart | 3 | Pendiente de validación Fase 3 | — | — | En validación / Fase 3 |
| Farmacias Guadalajara | 4 | 870 | 100% | 100% | Validado / Fase 2 |

Las Fases 1 y 2 actualmente cubren **5 retailers y 20 categorías**. La primera corrida integral todavía debe validarse live antes de fijar su baseline global.

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

Walmart está implementado dentro de Fase 3 y pendiente de su primera validación live con Chrome existente.

Farmacias Guadalajara ya está validado y productivo dentro de Fase 2.

La Comer está implementado pero sigue fuera del runner principal hasta completar validación live.

## Producción por fases — recomendada para servidor de 4 GB

Para reducir consumo de memoria, la operación productiva se ejecuta y consolida por checkpoints:

```text
Fase 1 -> validar -> integrar al master -> liberar recursos
Fase 2 -> validar -> integrar al master -> liberar recursos
Fase 3 -> Bodega Aurrera + Walmart (implementada, pendiente de validación live)
```

Comandos de integración:

```powershell
& .\scripts\integrate_validated_phase.ps1 -Phase 1
& .\scripts\integrate_validated_phase.ps1 -Phase 2
& .\scripts\integrate_validated_phase.ps1 -Phase 3
```

Master incremental fechado:

```text
output\concentrado_productivo_total_YYYY-MM-DD.xlsx
```

Ejemplo para el 12 de octubre de 2026:

```text
output\concentrado_productivo_total_2026-10-12.xlsx
```

La fecha identifica la muestra/corrida iniciada por Fase 1. Las fases siguientes se agregan sobre ese mismo archivo mediante `output\checkpoints_validos\current_master.json`.

Cada fase integrada crea además un checkpoint inmutable en:

```text
output\checkpoints_validos\
```

La Fase 1 inicia una nueva construcción del master. La Fase 2 sólo se incorpora cuando el master ya contiene una Fase 1 válida. Una fase reejecutada reemplaza su propia partición y no duplica filas.

## Fase 3 — Bodega Aurrera + Walmart

Estado: **EN VALIDACIÓN LIVE**

Alcance:
- Bodega Aurrera: 2 categorías activas.
- Walmart: 3 categorías activas.
- Total Fase 3: 5 categorías.

Runner:

```powershell
& .\scripts\run_phase3_production.ps1
```

El flujo es secuencial para reducir presión de memoria:

```text
Chrome manual ya abierto
├── pestaña Bodega Aurrera
└── pestaña Walmart (SC Toreo / 11220)
        ↓
Bodega Aurrera se conecta por CDP
→ termina child process, Chrome permanece abierto
→ Walmart se conecta por CDP
→ termina child process, Chrome permanece abierto
→ validar 5 categorías
→ publicar output\concentrado_fase3.xlsx
```

Bodega Aurrera y Walmart reutilizan el mismo Chrome abierto manualmente mediante remote debugging. Deben quedar abiertas una pestaña de Bodega Aurrera y otra de Walmart. En Walmart debe estar seleccionado SC Toreo, tienda 2344, CP 11220. El runner no abre ni cierra Chrome y no resuelve verificaciones de identidad.

Cuando Fase 3 pase sus gates:

```powershell
& .\scripts\integrate_validated_phase.ps1 -Phase 3
```

Esto agrega exclusivamente Bodega Aurrera + Walmart al concentrado fechado activo sin volver a ejecutar Fase 1 o Fase 2.

## Producción integral validada

El flujo final del proyecto une Fase 1 + Fase 2 en una sola corrida fresca. Ejecuta primero Fase 1 y, sólo si pasa, ejecuta Fase 2. El master general se publica únicamente cuando ambas fases terminan correctamente y vuelven a pasar sus gates.

Requisito para Fase 2: dejar abiertas en el mismo Chrome con remote debugging una pestaña de Farmacias Guadalajara y otra de Soriana.

Comando:

```powershell
& .\scripts\run_validated_production.ps1
```

Salida vigente:

```text
output\concentrado_productivo_total.xlsx
```

Primera baseline global inmutable:

```text
output\muestra_inicial_valida_total.xlsx
output\muestra_inicial_valida_total.json
```

Cada corrida integral válida crea además un extracto histórico inmutable:

```text
output\extractos_validos\extracto_validado_YYYYMMDD_HHMMSS.xlsx
output\extractos_validos\extracto_validado_YYYYMMDD_HHMMSS.json
```

El índice acumulativo de corridas válidas es:

```text
output\extractos_validos\manifest.csv
```

El workbook general contiene:

- `Concentrado`: todas las filas productivas de las dos fases.
- `Resumen`: las 20 categorías con su fase de origen.
- `Retailers`: resumen agregado por retailer.
- `Control`: run ID, timestamps, totales de cada fase y estado productivo.

Si alguna fase o gate falla, no se publica un nuevo master general ni un nuevo extracto histórico. Los archivos válidos anteriores se conservan.

Estado actual del runner integral: **PENDIENTE DE PRIMERA VALIDACIÓN LIVE**.

## Fase 1 productiva

La primera corrida productiva está limitada a los retailers estables:

- Farmacias del Ahorro: todas las categorías activas.
- Ibarra Mayoreo: todas las categorías activas, conservando presentación, unidades por empaque y precio por pieza. Cuando existe CAJA, el precio unitario se calcula como precio de caja / artículos por caja; si sólo existe presentación individual, se registra como single item.
- Chedraui Polanco (tienda 232): todas las categorías activas.

El runner productivo es:

```powershell
& .\scripts\run_phase1_production.ps1
```

El archivo canónico de producción es:

```text
output\concentrado_productivo.xlsx
```

La primera corrida que publique correctamente también crea, una sola vez, la muestra base inmutable:

```text
output\fase1_muestra_inicial_valida.xlsx
```

Ese archivo representa la **primera muestra válida oficial de Fase 1** y no se sobrescribe en corridas futuras. Su metadata se guarda en `output\fase1_muestra_inicial_valida.json`.

El archivo productivo contiene las hojas `Concentrado`, `Resumen` y `Control`. La publicación es atómica: el archivo productivo sólo se reemplaza cuando todas las categorías cumplen sus gates de calidad. Si una categoría falla, queda `PARTIAL`, se bloquea o no alcanza la cobertura requerida, el master productivo anterior se conserva sin cambios y el detalle se guarda en `diagnostics\phase1_production\last_run.json`.

## Fase 2 productiva

Fase 2 incorpora inicialmente:

- **Farmacias Guadalajara**: 4 categorías activas usando **Chrome abierto manualmente por el usuario**. El script se conecta a esa sesión mediante CDP y navega las categorías sin abrir ni cerrar otro navegador. La categoría se considera aceptable con cobertura de catálogo **>= 90%**, siempre que todas las filas extraídas tengan SKU, precio actual y URL completos.
- **Soriana**: 6 categorías activas usando **Chrome abierto manualmente por el usuario**. El script se conecta a esa sesión mediante CDP, navega las categorías y recorre la paginación sin abrir ni cerrar otro navegador.


### Runner productivo Fase 2

Farmacias Guadalajara y Soriana ya cuentan con métodos de extracción validados. El runner productivo los reutiliza sin modificar su lógica de scraping.

Antes de ejecutar, deja abiertas en el mismo Chrome con remote debugging:

```text
https://www.farmaciasguadalajara.com/
https://www.soriana.com/
```

Ejecuta:

```powershell
& .\scripts\run_phase2_production.ps1
```

El archivo canónico de Fase 2 es:

```text
output\concentrado_fase2.xlsx
```

La primera corrida productiva de Fase 2 ya fue validada el **12 de octubre de 2026**:

- 10/10 categorías COMPLETE
- Farmacias Guadalajara: 870 productos
- Soriana: 2,127 productos
- Total Fase 2: **2,997 productos**
- SKU, precio y URL completos en las 2,997 filas
- Baseline inicial creada correctamente

La baseline original se conserva por trazabilidad:

```text
output\fase2_muestra_inicial_valida.xlsx
output\fase2_muestra_inicial_valida.json
```

Después de corregir y revalidar el nombre de producto de Farmacias Guadalajara, la corrida completa del **12 de octubre de 2026** cerró con:

- 10/10 categorías COMPLETE
- Farmacias Guadalajara: 870 productos y `product_name_invalid = 0`
- Soriana: 2,148 productos
- Total Fase 2: **3,018 productos**
- SKU, precio y URL completos en las 3,018 filas

Para crear la baseline corregida versionada sin sobrescribir la original:

```powershell
& .\scripts\create_phase2_corrected_baseline.ps1
```

Esto genera:

```text
output\fase2_muestra_inicial_valida_v2_2026-10-12.xlsx
output\fase2_muestra_inicial_valida_v2_2026-10-12.json
```

El workbook productivo contiene `Concentrado`, `Resumen` y `Control`.

La publicación es atómica. Si cualquiera de las 10 categorías falla sus gates, `concentrado_fase2.xlsx` no se reemplaza. El diagnóstico queda en:

```text
diagnostics\phase2_production\last_run.json
diagnostics\phase2_production\phase2_YYYYMMDD_HHMMSS.log
```

Gates productivos:

- **Farmacias Guadalajara**: cobertura por categoría >= 90%, SKU/precio/URL completos y ejecución exitosa.
- **Soriana**: recorrido de toda la paginación detectada, productos > 0 y SKU/precio/URL completos.

### Farmacias Guadalajara - navegador manual

1. Abre Google Chrome manualmente.
2. Entra a `https://www.farmaciasguadalajara.com/`.
3. Si el wrapper indica que remote debugging no está habilitado, en esa misma ventana abre `chrome://inspect/#remote-debugging` y habilita **Allow remote debugging for this browser instance**.
4. Para probar una sola categoría ejecuta:

```powershell
& .\scripts\test_farmacias_guadalajara_phase2.ps1 -Category cuidado-bucal
```

5. Si la prueba pasa, ejecuta las cuatro categorías:

```powershell
& .\scripts\test_farmacias_guadalajara_phase2.ps1
```

El script usa la misma pestaña/sesión mediante **CDP directo por WebSocket**. No usa `Playwright.connect_over_cdp()`, no abre otro navegador y no cierra Chrome.

Salida:

```text
output\farmacias_guadalajara_phase2_test.xlsx
```

### Soriana - navegador manual

1. Abre Google Chrome manualmente.
2. Entra a `https://www.soriana.com/`.
3. Si el wrapper indica que remote debugging no está habilitado, en esa misma ventana abre `chrome://inspect/#remote-debugging` y habilita **Allow remote debugging for this browser instance**.
4. Ejecuta:

```powershell
& .\scripts\test_soriana_manual_phase2.ps1
```

El script usa la misma pestaña/sesión, recorre las categorías configuradas y no llama `browser.close()`.

Una sola categoría:

```powershell
& .\scripts\test_soriana_manual_phase2.ps1 -Category cuidado-bucal
```

Salida:

```text
output\soriana_manual_phase2_test.xlsx
```

Los tests de Fase 2 son aislados y no modifican `output\concentrado_productivo.xlsx` ni `output\fase1_muestra_inicial_valida.xlsx`.

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
package_type
units_per_package
is_single_item
price_per_unit
single_item_presentation
single_item_price
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
- categoría y jerarquía;
- timestamp de captura.

Siempre que el retailer lo exponga de forma estable también se conservan:

- SKU;
- URL de producto;
- marca;
- contexto de tienda.

Para la mayoría de retailers se exige cobertura completa de SKU y URL. En **Farmacias San Pablo**, el criterio bloqueante es la cobertura de catálogo y precio/promoción; SKU y URL se consideran campos informativos. En la última validación, sin embargo, ambos quedaron completos en 232/232 productos.

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
higiene-bucal   303
lavanderia      352
Total           655
```

La validación del 8 de octubre de 2026 tuvo cobertura completa: higiene bucal 303/303 y lavandería 352/352, con precios requeridos completos, contexto de tienda Polanco 232 verificado, cero huecos de paginación y cero errores de orden de precios.

Prueba completa aislada:

```powershell
& .\scripts\test_chedraui.ps1 -Headed
```

Sin `-Category`, el runner ejecuta ambas categorías. Para una sola categoría puede usarse `-Category higiene-bucal` o `-Category lavanderia`.

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

Regla crítica: se conserva el precio observado de la presentación de venta y su economía unitaria. Si existe **CAJA**, se registra el número de artículos y `price_per_unit = price_current / units_per_package`. Si la ficha sólo ofrece una presentación individual de 1 unidad, se registra como `is_single_item = true`. Cuando Ibarra publica además precio de Pieza/Paquete individual, se conserva en `single_item_price` para comparar el costo unitario de la caja contra la compra individual.

Cobertura de precio: 574/574. Cobertura de SKU: 570/574. En Fase 1 productiva, cada categoría de Ibarra debe cubrir al menos **90% del catálogo publicado**. `units_per_package` y `price_per_unit` son campos bloqueantes para todas las filas extraídas: si falta cualquiera de ellos, la categoría queda `PARTIAL`. Cuando la primera pasada queda por debajo del target, el scraper realiza una segunda pasada de descubrimiento antes de aplicar el umbral.

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

Validado en Fase 2 mediante Chrome abierto manualmente + CDP directo por WebSocket. La corrida completa validada obtuvo 870/870 productos en 4 categorías con SKU, precio y URL completos.

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

### 8 de octubre de 2026

- Chedraui Polanco (tienda 232, CP 11500) quedó revalidado con perfil persistente y precios estructurados VTEX `productSearchV3`.
- Higiene bucal: 303/303 productos, 297/297 precios requeridos.
- Lavandería: 352/352 productos, 338/338 precios requeridos.
- Ambas categorías terminaron `COMPLETE`, con cobertura 1.0, cero huecos de paginación y cero errores de orden de precios.
- Se agregó `scripts/test_chedraui.ps1`; por defecto ejecuta todas las categorías activas de Chedraui.

### 2 de octubre de 2026

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
