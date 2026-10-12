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
- CORRECCIÓN PENDIENTE DE VALIDACIÓN LIVE
- no considerar corregida la baseline hasta que Guadalajara vuelva a pasar una prueba real con nombres válidos

## Regla de mantenimiento

Cuando una corrección pase una prueba real:

1. Marcar el método como VALIDADO en este archivo.
2. Registrar el commit que contiene la solución estable.
3. Registrar el comando de prueba que pasó.
4. Registrar el criterio de calidad y el resultado observado.
5. No sustituir ese método por uno nuevo sin una razón concreta.
6. Probar cualquier mejora de forma aislada antes de reemplazar el patrón validado.
7. Si una prueba falla, no marcar el nuevo enfoque como validado y volver al último método exitoso.
