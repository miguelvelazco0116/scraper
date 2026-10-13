# Patrones de scraping validados

## Regla global: método validado, datos diarios

- `VALIDATED_METHOD` significa que la estrategia técnica ya pasó una prueba live y no debe rediseñarse sin una falla nueva concreta.
- La validación del método NO autoriza reutilizar precios o productos de días anteriores.
- Cada corrida diaria vuelve a extraer los datos y genera una captura asociada a `YYYY-MM-DD`.
- Fase 1 abre la fecha diaria.
- Fase 2 y Fase 3 deben coincidir con esa misma fecha.
- Los componentes de Fase 3 se guardan en `output\validated_phase3_components\YYYY-MM-DD\`.
- El integrador rechaza cruces de fecha.
- Los conteos observados son snapshots diarios, nunca gates estáticos.

Registro autoritativo de estado por fase: `docs/phase_status.json`.

Este archivo registra los enfoques que ya fueron probados con éxito en el entorno productivo del proyecto. Antes de modificar un retailer, revisar primero este registro y reutilizar el último patrón validado. Un método sólo pasa a VALIDADO después de una prueba real reportada como exitosa.

## Entorno de referencia

- Windows Server 2022
- 2 vCPU
- 4 GB RAM
- PowerShell
- Entorno virtual: C:\Proyectos\venvs\scraper
- Proyecto: C:\Proyectos\scraper

## Fase 1

### Farmacias del Ahorro

Estado: VALIDADO

Método:
- scraper dedicado actual
- categorías activas completas
- publicación atómica dentro de Fase 1

Resultado de referencia:
- congestion-nasal: 55/55
- preservativos: 73/73
- enjuagues-bucales: 45/45
- cremas-dentales: 115/115

Regla:
- cobertura completa
- SKU completo
- precio completo
- URL completa

### Ibarra Mayoreo

Estado: VALIDADO

Método:
- Playwright headless
- low-memory
- NO usar `context.route()` para bloquear recursos en Ibarra
- bloqueo de imágenes/fuentes mediante flags de Chrome
- reciclaje completo de navegador + BrowserContext durante recorridos largos
- reintentos de navegación
- recovery de descubrimiento sólo cuando cobertura inicial <90%

Reglas:
- cobertura mínima aceptable por categoría: 90%
- precio completo en todas las filas extraídas
- URL completa
- units_per_package completo
- price_per_unit completo
- SKU incompleto no bloquea cuando el sitio no lo expone

Campos económicos añadidos:
- package_type
- units_per_package
- is_single_item
- price_per_unit
- single_item_presentation
- single_item_price

Validación productiva de referencia:
- detergentes-lavatrastes-jab-abarrotes: 152/152
- detergentes-lavatrastes-jab-marca-propia: 2/2
- dentifricos-abarrotes: 86/86
- perfumeria-abarrotes: 332/333 = 99.7%

### Chedraui

Estado: VALIDADO

Método:
- Playwright headless
- perfil persistente .chedraui_profile
- low-memory
- bloqueo de image/media/font
- espera explícita de catálogo
- reintentos estructurados
- store context verificado

Resultado productivo de referencia más reciente:
- higiene-bucal: 299/299
- lavanderia: 339/339

Reglas:
- target completo
- SKU completo
- URL completa
- precio requerido completo
- store_context_verified = True
- structured_price_missing_pages = 0
- pagination_gaps = 0

## Fase 1 productiva

Estado: VALIDADO

Master:
- output\concentrado_productivo.xlsx

Baseline inmutable:
- output\fase1_muestra_inicial_valida.xlsx

Primera muestra válida:
- 1,527 filas
- 10/10 categorías COMPLETE

Regla de operación:
- futuras corridas actualizan concentrado_productivo.xlsx
- la baseline inicial nunca se sobrescribe

## Fase 1 - nueva muestra productiva válida 2026-10-12

Estado: VALIDADO / PRODUCTIVO

Corrida:
- comando: `& .\scripts\run_phase1_production.ps1`
- 10/10 categorías COMPLETE
- 1,498 filas productivas
- SKU/precio/URL completos en las 1,498 filas observadas
- baseline Fase 1 creada y marcada como muestra inicial válida

Detalle:
- Farmacias del Ahorro: 288 productos
  - congestion-nasal: 55/55
  - preservativos: 73/73
  - enjuagues-bucales: 45/45
  - cremas-dentales: 115/115
- Ibarra Mayoreo: 572 productos
  - detergentes-lavatrastes-jab-abarrotes: 152/152
  - detergentes-lavatrastes-jab-marca-propia: 2/2
  - dentifricos-abarrotes: 86/86
  - perfumeria-abarrotes: 332/333 = 99.7%, aceptado por gate >=90%
- Chedraui: 638 productos
  - higiene-bucal: 299/299
  - lavanderia: 339/339

Evidencia:
- `docs/validation_results/phase1_productive_valid_2026-10-12_144858.json`

Regla:
- estos conteos son una fotografía del catálogo y no son gates fijos
- esta muestra es válida aunque difiera de la baseline histórica anterior por cambios reales de catálogo

## Consolidación incremental por fases

Estado: CANDIDATO A VALIDACIÓN LIVE

Objetivo:
- reducir presión de RAM en Windows Server 2022 / 4 GB
- ejecutar y cerrar una fase antes de iniciar la siguiente
- integrar únicamente outputs productivos ya validados

Master incremental fechado:
- `output\concentrado_productivo_total_YYYY-MM-DD.xlsx`
- ejemplo: `output\concentrado_productivo_total_2026-10-12.xlsx`
- la fecha queda fijada cuando se integra Fase 1
- Fase 2 y futuras fases leen `output\checkpoints_validos\current_master.json` para continuar sobre ese mismo master

Checkpoints inmutables:
- `output\checkpoints_validos\checkpoint_fase1_YYYYMMDD_HHMMSS.xlsx`
- `output\checkpoints_validos\checkpoint_fase2_YYYYMMDD_HHMMSS.xlsx`
- `output\checkpoints_validos\checkpoint_fase3_YYYYMMDD_HHMMSS.xlsx`

Comandos:
- integrar Fase 1: `& .\scripts\integrate_validated_phase.ps1 -Phase 1`
- integrar Fase 2: `& .\scripts\integrate_validated_phase.ps1 -Phase 2`
- integrar Fase 3: `& .\scripts\integrate_validated_phase.ps1 -Phase 3`

Reglas:
- Fase 1 inicia/reinicia el master de la nueva corrida
- Fase 2 exige que Fase 1 ya esté integrada
- Fase 3 exige que Fase 1 y Fase 2 ya estén integradas
- reejecutar una fase reemplaza su propia partición; no duplica retailers
- cada integración valida nuevamente el workbook de la fase antes de publicarlo
- el master usa `CHECKPOINT_VALIDATED` mientras se construye por fases
- no mantener procesos de scraping de dos fases simultáneamente

Fase 3 acordada:
- Bodega Aurrera
- Walmart
- ya integrada técnicamente al consolidator mediante `-Phase 3`
- permanece PENDIENTE DE VALIDACIÓN LIVE antes de incorporarla al master oficial

## Incidencia Bodega Aurrera - usar fuentes /browse directas

Detectada el 2026-10-12 durante la validación de Fase 3.

Problema:
- las páginas padre configuradas podían mostrar únicamente el banner de categoría
- no garantizaban acceso directo al grid de productos

Fuentes directas confirmadas manualmente:
- cuidado-bucal / pasta dental:
  `https://www.bodegaaurrera.com.mx/browse/cuidado-personal/cuidado-bucal/pasta-dental/264479_950014_950022`
- cuidado-bucal / enjuagues bucales:
  `https://www.bodegaaurrera.com.mx/browse/cuidado-personal/cuidado-bucal/enjuagues-bucales/264479_950014_950025`
- cuidado-de-la-ropa / detergente en polvo:
  `https://www.bodegaaurrera.com.mx/browse/cuidado-de-la-ropa/detergente/detergente-en-polvo/3680083_3570057_3570059`
- cuidado-de-la-ropa / detergente líquido:
  `https://www.bodegaaurrera.com.mx/browse/cuidado-de-la-ropa/detergente/detergente-liquido/3680083_3570057_3570058`

Patrón candidato:
- mantener 2 categorías de negocio en el concentrado
- cada categoría puede declarar varias `source_urls`
- cuando existen `source_urls`, NO descubrir subcategorías desde el banner padre
- navegar directamente todas las fuentes configuradas
- descubrir y recorrer recursivamente todas las páginas publicadas de cada fuente
- navegación estrictamente secuencial
- pausa de 2.5 segundos entre páginas y 4 segundos entre categorías
- máximo de seguridad 100 páginas por fuente/categoría; llegar al límite sin demostrar cobertura impide validar

Gate de cobertura Fase 3:
- si el sitio publica un target confiable: cobertura >= 90% es aceptable
- si no publica target: se exige `pagination_verified=True`
- SKU/precio/URL de las filas extraídas siguen exigiéndose al 100%
- Walmart además exige contexto de tienda al 100%

Estado:
- PENDIENTE DE VALIDACIÓN LIVE

## Incidencia Bodega Aurrera - paginador UI debajo del catálogo

Detectada durante validación live el 2026-10-12.

Evidencia visual:
- el sitio muestra banners patrocinados dentro del flujo de la categoría
- esos banners contienen CTA `Ver más` y enlaces reales `/ip/`
- el paginador real está debajo del grid y se muestra como `1 2 3 … N >`

Corrección:
- en fuentes `/browse/` NO hacer clic en ningún `Ver más`
- ignorar productos cuyo ancestro esté marcado o contenga `Patrocinado por` / `Sponsored by`
- hacer scroll gradual hasta localizar un contenedor de paginación con controles numéricos
- leer la última página publicada, por ejemplo `23`
- recorrer estrictamente una página a la vez usando el control numérico siguiente o la flecha de siguiente
- después de cada clic exigir evidencia de avance mediante nueva página del paginador o nuevos SKUs
- mantener pausa de 2.5 s y ejecución secuencial
- registrar `pagination_method=ui_numeric_next`, `pages_visited` y `sponsored_banner_clicks=0`

Gate:
- con target confiable, coverage >=90% sigue siendo válido
- sin target, se exige completar el paginador UI
- si el control no avanza realmente, `pagination_verified=False`

Estado:
- PENDIENTE DE VALIDACIÓN LIVE

## Bodega Aurrera - corrida 487 filas descartada por paginación incompleta

Resultado observado durante validación live:
- cuidado-bucal: 235 filas, reportó 3+3 páginas
- cuidado-de-la-ropa: 252 filas, reportó 5+3 páginas
- SKU/precio/URL completos y price_validation_errors=0
- exit_code=0

Motivo de descarte:
- evidencia visual mostró paginador real con formato `1 2 3 … 23 >`
- el detector anterior calculaba `max_page` sólo con controles numéricos clicables visibles
- el último número después de la elipsis podía ser texto/span, por lo que se tomó erróneamente `3` como última página
- por esta razón `pagination_verified=True` fue un falso positivo

Corrección:
- combinar números de controles clicables con números del texto corto del contenedor del paginador
- tratar el último número después de la elipsis como `pager_max_page`
- para páginas no visibles como botón, avanzar con el control inmediatamente a la derecha de los números
- exigir avance real del paginador o SKUs nuevos

Estado:
- NO VALIDADO
- NO INTEGRAR a Fase 3

## Bodega Aurrera - paginación dinámica por estado actual

Regla operativa:
- el número de páginas N nunca se considera estático
- el scraper relee el paginador en cada página visitada
- `pager_max_page` / `last_page_observed` son diagnósticos dinámicos, no constantes
- la condición real para continuar es que exista un control "siguiente" habilitado
- el control siguiente puede detectarse por texto/aria (`Next`, `Siguiente`, `>`) o geométricamente como el control habilitado inmediatamente a la derecha de los números
- si el sitio cambia de 3 a 23, 31, 7 o cualquier otro número de páginas, el recorrido se adapta durante esa misma corrida
- `max_pages=100` es únicamente un tope de seguridad para evitar loops infinitos; nunca representa el total esperado
- llegar a ese tope con "siguiente" todavía habilitado invalida `pagination_verified`

Estado:
- PENDIENTE DE VALIDACIÓN LIVE

## Walmart - fuentes /browse directas y paginación dinámica

Configuración candidata 2026-10-12.

Fuentes directas confirmadas por navegación manual:
- cuidado-de-la-ropa / detergente en polvo:
  `https://www.walmart.com.mx/browse/cuidado-de-la-ropa/detergente/detergente-en-polvo/3680083_3570057_3570059`
- cuidado-de-la-ropa / detergente líquido:
  `https://www.walmart.com.mx/browse/cuidado-de-la-ropa/detergente/detergente-liquido/3680083_3570057_3570058`
- cuidado-bucal / pasta dental:
  `https://www.walmart.com.mx/browse/cuidado-personal/cuidado-bucal/pasta-dental/264479_950014_950022`
- cuidado-bucal / enjuagues bucales:
  `https://www.walmart.com.mx/browse/cuidado-personal/cuidado-bucal/enjuagues-bucales/264479_950014_950025`

Patrón:
- reutilizar Chrome abierto manualmente por CDP
- conservar contexto SC Toreo / store 2344 / CP 11220
- cuidado-bucal usa 2 `source_urls`
- cuidado-de-la-ropa usa 2 `source_urls`
- depilacion-y-rasurado se conserva como tercera categoría con su URL actual hasta contar con fuentes más específicas
- ignorar banners patrocinados dentro del catálogo
- hacer scroll hasta el paginador numérico real
- releer el paginador en cada página
- avanzar una página a la vez mientras exista control siguiente habilitado
- `max_pages=100` es sólo tope anti-loop
- pausa de 2.5 s entre páginas y 4 s entre fuentes/categorías

Gate:
- target confiable -> coverage >=90%
- sin target -> pagination_verified=True
- SKU/precio/URL de filas extraídas =100%
- store_context_verified/store_context_complete =100%

Estado:
- PENDIENTE DE VALIDACIÓN LIVE

## Bodega Aurrera - prueba terminal de página corregida

Diagnóstico live:
- Pasta Dental: 23/23 páginas, `pagination_complete=True`
- Enjuagues Bucales: 10/10 páginas alcanzadas
- última página Enjuagues:
  - `last_page_number=10`
  - `last_page_observed=10`
  - `last_has_next=False`
  - `last_next_enabled=False`
  - `last_new_unique_rows=0`
  - `last_click_method=next`
- el resultado anterior marcó `pagination_complete=False` únicamente porque la última página no añadió SKUs nuevos y `pager_current` llegó vacío

Corrección:
- una página terminal se considera probada cuando:
  - `page_number >= observed_last_page`
  - y `next_enabled=False`
- esa prueba terminal puede validar la última página aunque `new_unique_rows=0`
- no se relajan SKU/precio/URL ni los demás gates
- el test de Bodega ahora acepta `-Category cuidado-bucal` para no repetir categorías ya validadas

Estado:
- cuidado-de-la-ropa permanece VALIDADO
- cuidado-bucal requiere una corrida aislada final con la regla corregida
- Bodega completa aún NO se marca VALIDADA hasta esa corrida

## Fase 3 - consolidación sin re-scraping

Regla:
- una categoría aprobada se congela inmediatamente como componente validado
- una prueba posterior de otra categoría no puede sustituir ni borrar ese éxito
- el workbook temporal de test NO es la fuente final de consolidación
- `concentrado_fase3.xlsx` se construye exclusivamente desde componentes validados

Directorio estable:
- `output\validated_phase3_components\`

Checkpoints inmutables:
- `output\checkpoints_validos\phase3_components\`

Comandos:
- promover: `scripts\promote_phase3_component.ps1`
- construir Fase 3: `scripts\build_phase3_from_validated_components.ps1`
- integrar al master: `scripts\integrate_validated_phase.ps1 -Phase 3`

Componentes requeridos:
- Bodega Aurrera / cuidado-bucal
- Bodega Aurrera / cuidado-de-la-ropa
- Walmart / cuidado-bucal
- Walmart / cuidado-de-la-ropa
- Walmart / depilacion-y-rasurado

La construcción de Fase 3:
- no abre Chrome
- no consulta retailers
- no modifica Fase 1 o Fase 2
- falla si falta cualquier componente

## Fase 3 - Bodega Aurrera + Walmart

Estado: EN VALIDACIÓN LIVE

Alcance:
- Bodega Aurrera
  - cuidado-bucal
  - cuidado-de-la-ropa
- Walmart
  - cuidado-bucal
  - cuidado-de-la-ropa
  - depilacion-y-rasurado

Total:
- 2 retailers
- 5 categorías

Runner:
- `scripts\run_phase3_production.ps1`
- `scripts\run_phase3_production.py`
- comando: `& .\scripts\run_phase3_production.ps1`
- el wrapper descubre `DevToolsActivePort` del Chrome existente

Validaciones Bodega Aurrera:
- status SUCCESS en las 2 categorías
- productos > 0
- si hay target: coverage >= 90%
- si no hay target: pagination_verified=True
- SKU completo en todas las filas extraídas
- precio completo en todas las filas extraídas
- URL completa en todas las filas extraídas
- todas las fuentes directas configuradas visitadas
- price_validation_errors=0
- blocked_detected=False

Validaciones Walmart:
- 3/3 categorías COMPLETE
- productos > 0
- si hay target: coverage >= 90%
- si no hay target: pagination_verified=True
- SKU/precio/URL completos en todas las filas extraídas
- store_context_complete=products
- store_context_verified=True
- tienda: SC Toreo
- store_id: 2344
- CP: 11220
- price_order_errors=0
- duplicates_sku_url=0

Navegador y memoria:
- Chrome lo abre manualmente el usuario antes de iniciar la Fase 3
- remote debugging debe estar habilitado
- deben existir una pestaña de Bodega Aurrera y otra de Walmart
- Bodega se conecta por CDP a su pestaña existente; no abre ni cierra Chrome
- después termina su child process y libera Python/Playwright, conservando Chrome
- Walmart se conecta por CDP a su pestaña existente; no abre ni cierra Chrome
- Walmart debe conservar SC Toreo / tienda 2344 / CP 11220
- no se lanzan navegadores adicionales para Fase 3

Salida candidata:
- `output\concentrado_fase3.xlsx`

Baseline:
- `output\fase3_muestra_inicial_valida.xlsx`
- sólo se crea en la primera corrida Fase 3 que pase todos los gates

Integración:
- `& .\scripts\integrate_validated_phase.ps1 -Phase 3`
- agrega únicamente Bodega Aurrera + Walmart al master fechado activo
- no modifica las particiones de Fase 1 o Fase 2

Regla:
- NO marcar Fase 3 como VALIDADA hasta observar una corrida live completa en Windows Server

## Fase 2

### Farmacias Guadalajara

Estado: VALIDADO

Patrón oficial:
- Chrome abierto manualmente por el usuario
- remote debugging habilitado
- CDP directo por WebSocket
- NO usar Playwright.connect_over_cdp(), porque en Windows Server se observó timeout después de establecer el WebSocket
- no abrir otro Chrome
- no cerrar la sesión del usuario
- expansión de catálogo mediante clicks CDP sobre "Ver más productos"

Commit estable del método:
- 52ebb6f921480da0aff0091fa610605ff7edd741

Comando validado:
- & .\scripts\test_farmacias_guadalajara_phase2.ps1 -Category cuidado-bucal

Validación real reportada el 2026-10-11:
- categoría: cuidado-bucal
- target publicado: 358
- links detectados: 358
- productos extraídos: 358
- cobertura: 100%
- SKU completos: 358/358
- precios completos: 358/358
- URLs completas: 358/358
- rondas de expansión: 17
- patrón de expansión: 20 -> 40 -> ... -> 340 -> 358
- método de click: cdp_mouse_ranked
- return_code: 0
- resultado: SUCCESS

Validación completa reportada el 2026-10-12:
- vias-respiratorias: 284/284, 100% cobertura, SKU/precio/URL 284/284
- lavanderia: 170/170, 100% cobertura, SKU/precio/URL 170/170
- cuidado-bucal: 358/358, 100% cobertura, SKU/precio/URL 358/358
- preservativos: 58/58, 100% cobertura, SKU/precio/URL 58/58
- total: 870/870 productos
- 4/4 categorías: SUCCESS
- return_code: 0 en todas
- resultado retailer: PRODUCTIVO / VALIDADO

Gate oficial:
- cobertura >= 90%
- SKU completo en filas extraídas
- precio completo
- URL completa

Scripts oficiales:
- scripts\test_farmacias_guadalajara_phase2.ps1
- scripts\test_farmacias_guadalajara_phase2.py
- scripts\scrape_farmacias_guadalajara_phase2_raw_cdp.py

Regla:
- no sustituir CDP directo por Playwright.connect_over_cdp() salvo que el método validado deje de funcionar y exista una prueba aislada que demuestre una alternativa mejor.

### Soriana

Estado: VALIDADO / PRODUCTIVO

Patrón oficial:
- Chrome abierto manualmente por el usuario
- remote debugging habilitado
- conexión a la sesión existente
- no abrir otro navegador
- no cerrar Chrome
- navegación y paginación sobre la misma sesión

Comando validado:
- & .\scripts\test_soriana_manual_phase2.ps1

Validación completa reportada el 2026-10-12:
- cuidado-bucal: 348 productos, 15 páginas, SKU/precio/URL 348/348
- limpiadores: 1,075 productos, 45 páginas, SKU/precio/URL 1,075/1,075
- detergentes: 197 productos, 9 páginas, SKU/precio/URL 197/197
- afeitado-depilacion-dama: 59 productos, 3 páginas, SKU/precio/URL 59/59
- desodorantes-para-caballero: 273 productos, 12 páginas, SKU/precio/URL 273/273
- desodorantes-para-dama: 196 productos, 9 páginas, SKU/precio/URL 196/196
- total: 2,148 productos
- total páginas detectadas: 93
- 6/6 categorías: COMPLETE
- target publicado: no disponible en estas categorías

Criterio oficial mientras target_products no esté disponible:
- recorrer toda la paginación detectada
- productos > 0
- SKU completo en todas las filas extraídas
- precio completo
- URL completa

Regla:
- no cambiar este patrón mientras continúe funcionando.
- cualquier alternativa debe probarse de forma aislada antes de sustituir el método validado.

## Fase 2 productiva

Estado: VALIDADO / PRODUCTIVO

Runner oficial:
- scripts\run_phase2_production.ps1
- scripts\run_phase2_production.py

Métodos reutilizados:
- Farmacias Guadalajara: Chrome manual + CDP directo por WebSocket + cdp_mouse_ranked
- Soriana: Chrome manual + sesión existente + paginación detectada completa

Commit productivo base:
- 8e1dd24010e2627407000382c7f94f032a98210c

Hardening de retry Guadalajara:
- f5e930684bb46cd434606cfb26702d27480ca7fa

Validación productiva real reportada el 2026-10-12:
- 10/10 categorías COMPLETE
- Farmacias Guadalajara: 870 productos
- Soriana: 2,127 productos
- total Fase 2: 2,997 productos
- SKU completos: 2,997/2,997
- precios completos: 2,997/2,997
- URLs completas: 2,997/2,997
- publicación atómica: exitosa
- baseline inicial: creada

Detalle Guadalajara:
- vias-respiratorias: 284/284
- lavanderia: 170/170
- cuidado-bucal: 358/358
- preservativos: 58/58

Detalle Soriana:
- cuidado-bucal: 327 productos, 15 páginas
- limpiadores: 1,075 productos, 45 páginas
- detergentes: 197 productos, 9 páginas
- afeitado-depilacion-dama: 59 productos, 3 páginas
- desodorantes-para-caballero: 273 productos, 12 páginas
- desodorantes-para-dama: 196 productos, 9 páginas

Master productivo:
- output\concentrado_fase2.xlsx

Baseline inmutable:
- output\fase2_muestra_inicial_valida.xlsx
- output\fase2_muestra_inicial_valida.json

Regla:
- concentrado_fase2.xlsx puede actualizarse en futuras corridas válidas
- fase2_muestra_inicial_valida.xlsx no debe sobrescribirse
- si alguna categoría falla, el master anterior se conserva
- no sustituir los métodos validados de Guadalajara o Soriana sin una prueba aislada exitosa

Revalidación corregida del 2026-10-12:
- 10/10 categorías COMPLETE
- Farmacias Guadalajara: 870 productos
- Guadalajara product_name_complete: 870/870
- Guadalajara product_name_invalid: 0
- Soriana: 2,148 productos
- total Fase 2: 3,018 productos
- SKU completos: 3,018/3,018
- precios completos: 3,018/3,018
- URLs completas: 3,018/3,018
- concentrado_fase2.xlsx publicado correctamente
- baseline original conservada sin sobrescribir

Baseline corregida versionada:
- output\fase2_muestra_inicial_valida_v2_2026-10-12.xlsx
- output\fase2_muestra_inicial_valida_v2_2026-10-12.json
- se crea únicamente mediante scripts\create_phase2_corrected_baseline.ps1
- el creador vuelve a validar las 10 categorías, SKU/precio/URL y los gates de nombre de Guadalajara antes de copiar el master

Creación confirmada en entorno real el 2026-10-12:
- filas: 3,018
- categorías: 10
- Farmacias Guadalajara: 870 productos
- nombres inválidos Guadalajara: 0
- baseline v2 creada correctamente
- metadata v2 creada correctamente
- baseline original conservada sin sobrescribir

Referencia oficial corregida de Fase 2:
- output\fase2_muestra_inicial_valida_v2_2026-10-12.xlsx

Referencia histórica:
- output\fase2_muestra_inicial_valida.xlsx

## Fase 2 - nueva muestra productiva válida 2026-10-12

Estado: VALIDADO / PRODUCTIVO

Corrida:
- comando: `& .\scripts\run_phase2_production.ps1`
- 10/10 categorías COMPLETE
- 3,018 filas productivas
- Farmacias Guadalajara: 870 productos
- Soriana: 2,148 productos
- SKU/precio/URL completos en las 3,018 filas observadas

Farmacias Guadalajara:
- vias-respiratorias: 284/284
- lavanderia: 170/170
- cuidado-bucal: 358/358
- preservativos: 58/58
- product_name_complete: 870/870
- product_name_invalid: 0

Soriana:
- cuidado-bucal: 348 productos, 15 páginas
- limpiadores: 1,075 productos, 45 páginas
- detergentes: 197 productos, 9 páginas
- afeitado-depilacion-dama: 59 productos, 3 páginas
- desodorantes-para-caballero: 273 productos, 12 páginas
- desodorantes-para-dama: 196 productos, 9 páginas

Evidencia:
- `docs/validation_results/phase2_productive_valid_2026-10-12_165207.json`

Regla:
- estos conteos son fotografía del catálogo y no son gates fijos
- esta muestra está aprobada para integración incremental sobre la Fase 1 válida

## Incidencia de calidad - Farmacias Guadalajara product

Detectada el 2026-10-12:
- la columna `product` de la primera baseline productiva de Fase 2 quedó etiquetada como `Price reduced from` en registros de Farmacias Guadalajara
- SKU, precio, URL, cobertura y expansión no presentan este defecto
- causa: el extractor seleccionaba el primer anchor de producto y podía tomar el enlace accesible del precio antes que el nombre del artículo

Corrección implementada:
- priorizar atributos y nodos semánticos de nombre de producto
- usar `img alt` y anchors equivalentes como respaldo
- rechazar etiquetas de precio como `Price reduced from`
- usar el slug de URL sólo como último fallback
- añadir gate bloqueante `product_name_invalid = 0`

Estado:
- CORRECCIÓN VALIDADA LIVE el 2026-10-12
- la muestra aislada de Farmacias Guadalajara ya devuelve nombres reales de producto
- `product_name_invalid = 0`
- SKU, precio, URL, cobertura y expansión permanecen correctos
- el fix estable está en el commit `d7615c34f59d8157c9a41a7e8994d50732e4e17e`
- la baseline original de Fase 2 no se sobrescribe automáticamente; tras una corrida completa válida se debe crear una referencia corregida conservando la evidencia histórica anterior

## Producción integral Fase 1 + Fase 2

Estado: REEMPLAZADA OPERATIVAMENTE POR CONSOLIDACIÓN INCREMENTAL EN SERVIDOR DE 4 GB

Nota:
- el runner monolítico se conserva como referencia histórica
- para producción actual usar ejecución por fases + `integrate_validated_phase.ps1`
- razón: liberar navegador/Python/Playwright entre fases y reducir presión de memoria


Runner:
- scripts\run_validated_production.ps1
- scripts\run_validated_production.py

Método:
- ejecutar Fase 1 productiva completa
- abortar si Fase 1 falla
- ejecutar Fase 2 productiva completa sobre Chrome manual
- volver a leer y validar los workbooks publicados por ambas fases
- exigir 10/10 categorías COMPLETE en Fase 1
- exigir 10/10 categorías COMPLETE en Fase 2
- volver a exigir product_name_invalid = 0 para Farmacias Guadalajara
- consolidar exclusivamente las dos corridas frescas exitosas
- publicar desde un staging validado

Salidas:
- master vigente: output\concentrado_productivo_total.xlsx
- baseline global: output\muestra_inicial_valida_total.xlsx
- histórico: output\extractos_validos\extracto_validado_YYYYMMDD_HHMMSS.xlsx
- metadata por extracto: output\extractos_validos\extracto_validado_YYYYMMDD_HHMMSS.json
- índice histórico: output\extractos_validos\manifest.csv
- diagnóstico: diagnostics\production_master\last_run.json

Reglas:
- cada corrida válida genera un extracto histórico nuevo e inmutable
- el master vigente se reemplaza sólo después de validar ambas fases
- la primera baseline global se crea una sola vez
- nunca usar un output viejo de una fase para publicar el master cuando el child process de esa fase falla
- este runner sólo pasa a VALIDADO después de una corrida real exitosa en el Windows Server

## Incidencia Ibarra - Playwright event listener en LOW-MEMORY

Detectada el 2026-10-12 durante la primera corrida integral:
- la extracción alcanzó al menos 98/152 fichas de `detergentes-lavatrastes-jab-abarrotes`
- apareció `Error occurred in event listener` dentro de `playwright._impl._connection.dispatch`
- el descubrimiento de catálogo y el parser de detalle seguían funcionando hasta ese punto
- la causa más probable es una carrera de requests canceladas durante navegaciones rápidas mientras el callback `context.route("**/*")` intenta `abort()` o `continue_()`

Hardening aplicado:
- conservar exactamente el mismo bloqueo LOW-MEMORY de image/media/font
- conservar paginación, parser, precios, unidades y reciclaje de renderer
- capturar `PlaywrightError` únicamente dentro del callback de routing
- los fallos reales de navegación siguen siendo detectados por `_goto()`

Estado:
- VALIDADO LIVE el 2026-10-12
- categoría: detergentes-lavatrastes-jab-abarrotes
- modo: HEADLESS + LOW-MEMORY
- target_products: 152
- last_page: 13
- product_links: 152
- discovery_complete: True
- discovery_coverage: 1.0
- products_with_sale_price: 152
- products_with_box_price: 152
- unit_count_complete: 152
- unit_price_complete: 152
- products_without_box_price: 0
- parse_errors: 0
- resultado: SUCCESS
- total filas con precio utilizable: 152
- fix estable: `cce903d89f14b0f2deb1de99c5a017f92685af8d`

## Incidencia Ibarra - recovery innecesario con cobertura válida

Detectada el 2026-10-12 durante la primera corrida integral:
- perfumeria-abarrotes publicó target 333
- primera pasada detectó 332 productos
- cobertura: 99.7%
- el scraper inició una segunda pasada completa de 28 páginas sólo por 1 producto faltante
- durante esa recuperación aparecieron múltiples `socket.send() raised exception`

Ajuste:
- si la primera pasada queda >= 90% de cobertura, no ejecutar recovery
- conservar recovery únicamente para cobertura < 90%
- alinear el status interno del scraper con el gate oficial >= 90%
- mantener intactos descubrimiento, fichas, parser, precios, unidades, URLs y LOW-MEMORY

Estado:
- PENDIENTE DE VALIDACIÓN LIVE
- validar primero `perfumeria-abarrotes` en HEADLESS + LOW-MEMORY
- esperado para el caso observado: 332/333 aceptado como SUCCESS si no hay parse_errors

## Incidencia Ibarra - watchdog de detalle descartado

Prueba live del 2026-10-12 en `perfumeria-abarrotes`:
- target_products: 333
- product_links: 332
- products_with_sale_price: 27
- parse_errors: 305
- status: PARTIAL
- discovery_coverage calculada sobre filas útiles: 27/333 = 8.1%

Conclusión:
- el watchdog de detalle de 3 x 30 segundos degradó severamente la extracción
- NO VALIDADO
- NO USAR
- se revirtió por completo

Se conserva como baseline operativa:
- listener LOW-MEMORY fail-safe validado
- recovery omitido cuando la cobertura inicial es >= 90%
- navegación de detalle previa a este watchdog

Siguiente diagnóstico:
- leer los `parse_errors` guardados en `diagnostics/ibarra_mayoreo_perfumeria-abarrotes.json` antes de aplicar otro cambio

## Incidencia Ibarra - cierre de BrowserContext en categorías largas

Diagnóstico confirmado el 2026-10-12:
- `perfumeria-abarrotes` descubrió 332/333 links = 99.7% de cobertura
- los errores posteriores fueron `TargetClosedError: BrowserContext.new_page: Target page, context or browser has been closed`
- el problema está en la vida útil de la sesión Chrome/BrowserContext durante cientos de navegaciones, no en paginación, parser ni cobertura

Hardening aplicado:
- conservar la navegación validada `_goto()`
- conservar el listener LOW-MEMORY fail-safe
- conservar la regla de no recovery cuando cobertura inicial >= 90%
- reiniciar navegador + BrowserContext antes de comenzar las fichas en LOW-MEMORY
- reiniciar navegador + BrowserContext cada 50 fichas
- si ocurre `TargetClosedError` entre checkpoints, recrear la sesión y reintentar esa misma ficha una vez
- si el error no es de sesión cerrada, conservar el comportamiento de error existente
- separar correctamente `discovery_coverage = product_links / target` de las filas de detalle procesadas

Estado:
- PENDIENTE DE VALIDACIÓN LIVE
- validar primero `perfumeria-abarrotes` en HEADLESS + LOW-MEMORY
- esperado: product_links 332/333 (~99.7%), parse_errors=0 y reinicios de sesión visibles

## Incidencia Ibarra - OOM del driver Playwright por routing

Diagnóstico confirmado el 2026-10-12:
- log real: `Fatal process out of memory: Zone`
- Playwright lanzó `MemoryError` dentro de `connection.dispatch`
- el stack repetido termina en el callback LOW-MEMORY de Ibarra y `route.abort()`
- después aparecen errores repetidos de event listener y finalmente `socket.send() raised exception`
- por lo tanto el problema raíz no es una ficha específica ni sólo el BrowserContext: el proceso Node/V8 que usa Playwright agota memoria mientras mantiene routing interceptado

Cambio:
- eliminar completamente `context.route("**/*")` de Ibarra
- no usar `route.abort()` ni `route.continue_()` para LOW-MEMORY
- bloquear imágenes mediante Chrome: `--blink-settings=imagesEnabled=false`
- desactivar fuentes remotas mediante Chrome: `--disable-remote-fonts`
- conservar `--renderer-process-limit=2`
- conservar reciclaje completo Chrome + BrowserContext cada 50 fichas
- conservar recuperación de sesión cerrada y reintento de la misma ficha
- conservar parser, precios, unidades, SKU, URLs, paginación y gate >= 90%

Supersede:
- el hardening previo del listener LOW-MEMORY queda retirado para Ibarra; aunque pasó una categoría corta, el log de categoría larga demuestra que el routing no es seguro en el Windows Server de 4 GB

Estado:
- PENDIENTE DE VALIDACIÓN LIVE
- validar `perfumeria-abarrotes` en HEADLESS + LOW-MEMORY
- esperado: sin `MemoryError: Route.abort`, sin `Fatal process out of memory: Zone`, cobertura ~332/333, parse_errors=0

## Regla de mantenimiento

Cuando una corrección pase una prueba real:

1. Marcar el método como VALIDADO en este archivo.
2. Registrar el commit que contiene la solución estable.
3. Registrar el comando de prueba que pasó.
4. Registrar el criterio de calidad y el resultado observado.
5. No sustituir ese método por uno nuevo sin una razón concreta.
6. Probar cualquier mejora de forma aislada antes de reemplazar el patrón validado.
7. Si una prueba falla, no marcar el nuevo enfoque como validado y volver al último método exitoso.
