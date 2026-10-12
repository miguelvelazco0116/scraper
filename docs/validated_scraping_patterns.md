# Patrones de scraping validados

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
- bloqueo de image/media/font
- reciclaje del renderer durante recorridos largos
- reintentos de navegación
- segunda pasada de descubrimiento cuando falta catálogo

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

Resultado productivo de referencia:
- higiene-bucal: 304/304
- lavanderia: 363/363

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

Estado: EN VALIDACIÓN LIVE

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

## Regla de mantenimiento

Cuando una corrección pase una prueba real:

1. Marcar el método como VALIDADO en este archivo.
2. Registrar el commit que contiene la solución estable.
3. Registrar el comando de prueba que pasó.
4. Registrar el criterio de calidad y el resultado observado.
5. No sustituir ese método por uno nuevo sin una razón concreta.
6. Probar cualquier mejora de forma aislada antes de reemplazar el patrón validado.
7. Si una prueba falla, no marcar el nuevo enfoque como validado y volver al último método exitoso.
