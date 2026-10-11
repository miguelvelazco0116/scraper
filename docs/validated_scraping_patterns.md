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

Estado: EN VALIDACIÓN

Patrón acordado:
- Chrome abierto manualmente por el usuario
- scraper conectado a la sesión existente
- no abrir otro navegador
- no cerrar Chrome

Antes de modificar este retailer, reutilizar el último método manual que haya pasado una prueba real.

## Regla de mantenimiento

Cuando una corrección pase una prueba real:

1. Marcar el método como VALIDADO en este archivo.
2. Registrar el commit que contiene la solución estable.
3. Registrar el comando de prueba que pasó.
4. Registrar el criterio de calidad y el resultado observado.
5. No sustituir ese método por uno nuevo sin una razón concreta.
6. Probar cualquier mejora de forma aislada antes de reemplazar el patrón validado.
7. Si una prueba falla, no marcar el nuevo enfoque como validado y volver al último método exitoso.
