# Scraper

> **Registro de métodos validados:** antes de cambiar la estrategia de un retailer, revisar `docs/validated_scraping_patterns.md`. El estado autoritativo de cada fase vive en `docs/phase_status.json`. Los métodos sólo se marcan como VALIDADO después de una prueba real exitosa. multi-retailer

Proyecto modular para extraer catálogos públicos, precios y promociones de retailers en México y concentrar los resultados en un único Excel.

El proyecto se desarrolla y ejecuta **localmente en Windows**. GitHub se usa para control de versiones, respaldo e integración del código. Los scrapers usan Python, Playwright y/o Selenium según el retailer; cuando un sitio requiere navegador visible se utiliza Google Chrome local.

No se automatizan CAPTCHAs, verificaciones de identidad ni mecanismos para evadir controles de acceso.

## Panel Streamlit de operación (MVP previo a Azure SQL)

**Modo de ejecución local controlada**. Ahora existe la pantalla **Nueva ejecución** con botones reales por fase y retailer. El centro de control sigue consultando el master en modo lectura; únicamente los runners existentes pueden modificarlo cuando se escoge y confirma **Captura + integración**. La línea de comandos continúa disponible como alternativa.

### Arranque en Windows

```powershell
cd C:\Proyectos\scraper
git pull --ff-only origin main
& C:\Proyectos\venvs\scraper\Scripts\python.exe -m pip install -r .\requirements-dashboard.txt
& .\scripts\run_dashboard.ps1
```

Abrir http://127.0.0.1:8501 en el equipo. El launcher escucha solo en 127.0.0.1 (loopback); no publicar el puerto sin autenticación/TLS.

### Módulos

- **Resumen**: las cinco fases, 13 retailers y sus casos COMPLETE, reconciliación real entre resumen, datos y Control. Fuente: auditor existente `scripts/audit_master_phases.py`.
- **Calidad**: SKU, precio, URL, disponibilidad 1/0, contexto de tienda y hallazgos informativos por retailer con CSV. N/D significa sin evidencia, no es una falla automática; se mantienen los gates productivos por retailer.
- **Fallas**: histórico de errores y últimos logs de `diagnostics/`, con ocultación básica de secretos y lecturas acotadas. El estado del master determina qué está integrado realmente.
- **Nueva ejecución**: botones reales para las cinco fases y pruebas aisladas de los 13 retailers, confirmación explícita, botón Detener, un solo proceso a la vez, folio, log y seguimiento periódico. **Solo captura** es el modo inicial donde el runner permite `-SkipIntegration`. Fase 1 completa siempre integra por diseño; las pruebas por retailer nunca integran automáticamente.
- **Guía de comandos CLI**: sigue ofreciendo los comandos para ejecución manual y auditoría `-Preview`.
- **Protección de capturas**: se respalda el master/puntero antes de ejecutar una integración; las salidas existentes de prueba aislada se respaldan antes de sobrescribirse. Los SHA-256 del master antes/después quedan registrados en `diagnostics/dashboard_runs/*.json`. La ausencia de un master del día bloquea fases 4/5 completas, y las integraciones de fase 2/3/4/5 si no hay fases previas cerradas.
- **Azure SQL**: servidor `*.database.windows.net`, base, puerto, identidad y autenticación SQL/Entra Interactive; configuración no secreta en `config/local/azure_sql.json`, ignorada por Git. No guarda contraseñas. Prueba opcional por `pyodbc` con ODBC Driver 18 y consulta SELECT; `Encrypt=yes` y `TrustServerCertificate=no` obligatorios. **No migra datos, no crea tablas.**
- **Trazabilidad**: selector de masters históricos `concentrado_productivo_total_*.xlsx`, fechas reales, alcance por categoría, checkpoints y backups.

Conservamos `PUBLIC_ONLINE_CATALOGUE_NO_POSTAL_PRICE_CERTIFICATION` en Mayorista Duero: ninguna información se etiqueta como precio o inventario verificado por CP.

Pruebas del panel:

```powershell
& C:\Proyectos\venvs\scraper\Scripts\python.exe -m unittest discover -s tests -p "test_dashboard_helpers.py" -v
& C:\Proyectos\venvs\scraper\Scripts\python.exe -m unittest discover -s tests -p "test_dashboard_execution.py" -v
```

**Fecha y seguridad:** la captura de hoy nunca debe interpretarse como integrada en un master de ayer. Si el puntero conserva la fecha anterior, comienza por preparar la fase 1 del día; los runners restantes preservan los gates de fecha propios. El monitor de Streamlit no evade CAPTCHAs ni verifica precios/stock Duero por código postal; la opción `-ApproveDueroOnline` permanece fuera de la ejecución automática.

**Lanzamiento local:** solo escucha en `127.0.0.1`; no desplegar como servicio público sin autenticación, aislamiento de usuarios y autorización por acción. No ejecutar simultáneamente scripts por CLI mientras trabaja Streamlit: el bloqueo cubre **solo los jobs lanzados por el panel**. Los procesos independientes de PowerShell no comparten ese bloqueo. En Windows, la ejecución se realiza como el usuario de Windows que inició Streamlit.

### Fase 1: no duplicar capturas; ejecutar solo pendientes Chedraui

La pantalla **Nueva ejecución → Fase 1** muestra una matriz de **30 casos**
(6 Ahorro + 4 Ibarra + 20 Chedraui: 4 tiendas × 5 categorías). El estado
se calcula con `fase1_progress.xlsx` y los checkpoints independientes
`output/checkpoints_validos/chedraui_cases/`, exclusivamente **del día**.
Los registros Chedraui se revalidan por tienda, categoría, fecha, contrato
de contexto, SKU, precios y paginación. Un `COMPLETE` textual por sí solo
no autoriza reutilización. **COMPLETE de captura no implica integrado.**

El botón individual **Chedraui** ahora utiliza
`scripts/run_chedraui_incremental.ps1` en modo solo captura: omite los casos
COMPLETE, ejecuta `test_chedraui.py` solo para combinaciones pendientes y
conserva checkpoints que podrá reutilizar el runner productivo Fase 1.
No modifica el master.

Previsualizar sin navegar:

```powershell
& .\scripts\run_chedraui_incremental.ps1 -Preview
```

Ejecutar pendientes desde PowerShell (alternativa al botón):

```powershell
& .\scripts\run_chedraui_incremental.ps1 -Headed
```

**Detener desde Streamlit:** en Nueva ejecución → Seguimiento e historial →
selecciona el folio activo → `⏹ Detener ejecución`. La orden registra una
solicitud local de paro y termina el proceso PowerShell del folio y sus
descendientes; puede interrumpir archivos de prueba en curso, por lo que
solo se reutilizan casos ya certificados. Los checkpoints anteriores
se conservan. El paro no detiene programas externos iniciados por CLI.

**Worker anterior:** un worker que ya estaba corriendo no se actualiza
automáticamente al hacer `git pull`. Para solicitar su paro desde PowerShell
**después de actualizar el repositorio**, sin reiniciar primero la aplicación:

```powershell
python -c "from pathlib import Path; from dashboard.execution import current_active_job, request_stop; r=Path.cwd(); j=current_active_job(r); print('Folio activo:', j['run_id'] if j else 'ninguno'); request_stop(r,j['run_id']) if j else None"
```

Pruebas auxiliares:

```powershell
python -m pytest -q tests/test_dashboard_phase1.py tests/test_dashboard_execution.py
```

Antes de la migración productiva: definir esquema, historial, cargas idempotentes, privilegios mínimos, respaldos y rollback.


## Estado actual

Última actualización: **13 de octubre de 2026**.

Última muestra completa validada localmente:

| Retailer | Categorías activas | Productos validados | Precio actual | SKU/URL | Estado |
|---|---:|---:|---:|---:|---|
| Soriana | 6 | 2,148 | 100% | 100% | Validado / Fase 2 |
| Chedraui | 2 | 667 | 100% | 100% | Validado / Fase 1 |
| Farmacias del Ahorro | 4 | 288 | 100% | 100% | Validado / Fase 1 |
| Farmacias San Pablo | 4 | 232 | 100% | Informativo; 100% en validación previa | Validado / OCC capturado |
| Ibarra Mayoreo | 4 | 572 | 100% | SKU informativo; gates unitarios completos | Validado / Fase 1 |
| Bodega Aurrera | 2 | 1,883 | 100% | 100% | Método validado / muestra diaria válida |
| Farmacias Similares | 2 | 30 | 100% | 100% | Validado / Fase 5 |
| HEB | 2 | 272 validados + Lavandería pendiente | 100% en Higiene Dental | 100% en Higiene Dental | En validación / Fase 5 |
| La Comer | 2 | Pendiente | Pendiente | Pendiente | Implementado, no activo |
| Walmart | 3 | Pendiente de validación Fase 3 | — | — | En validación / Fase 3 |
| Farmacias Guadalajara | 4 | 870 | 100% | 100% | Validado / Fase 2 |

Las Fases 1 y 2 actualmente cubren **5 retailers y 20 categorías** y quedan preservadas como éxitos validados en `docs/phase_status.json`. Fase 3 sólo se añadirá a ese registro y al concentrado cuando pase una corrida live completa.

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

## Ejecución integral de Fase 5 (4 retailers, 13 categorías)

La corrida del día se realiza desde PowerShell tras abrir **dos pestañas en el mismo Chrome conectado por CDP**: `https://www.heb.com.mx/` con una tienda concreta seleccionada y `https://www.sams.com.mx/`. Si una tienda requiere una verificación manual, no se evade. Las fases 1-4 deben figurar como `COMPLETE` en el master fechado activo.

```powershell
cd C:\Proyectos\scraper
git pull --ff-only origin main
& .\scripts\run_phase5_production.ps1
```

`scripts/run_phase5_production.ps1` captura secuencialmente Farmacias Similares (2), HEB (2), Sam's Club (3) y Mayorista Duero (6), reutiliza los checkpoints del día cuando los respectivos scrapers lo permiten, audita paginación Shopify 50/250 y empaque/conteos storefront de Duero, previsualiza la integración, añade **solo retailers completos de hoy** y emite la auditoría de fases y retailers. Si un retailer falla, los otros siguen ejecutándose; la Fase 5 queda `PARTIAL_VALIDATED` hasta integrarlos todos. `-SkipIntegration` limita la operación a capturas, vista previa y auditoría; ninguna fase previa se vuelve a ejecutar.

**Duero no se aprueba como precio/stock por código postal automáticamente:** la corrida nunca pasa `-ApproveDueroOnline`. Para incorporarlo solamente como **catálogo online público**, el operador debe revisar los reportes y autorizar expresamente esa modalidad, por separado. Sin aprobación ni contexto de tienda certificado, el integrador lo mantiene `PENDIENTE`, incluso cuando los seis catálogos se extrajeron con éxito.

El log queda en `diagnostics/phase5_production/phase5_*.log`, las pruebas en `output/*phase5_test.xlsx` y los reportes de integración/auditoría en `output/reportes_integracion/`. El runner bloquea un master de otra fecha o que aún no tenga completas las fases 1-4.

## Auditoría de Mayorista Duero (Fase 5)

**Paginación reforzada:** una respuesta de Shopify con menos de 250
productos NO demuestra que se agotó el catálogo. La prueba solicita
**todas las páginas** de cada categoría en dos recorridos independientes:
primero `limit=50`, luego `limit=250`. Ambos deben encontrar una
**página terminal vacía explícita**, sin saltos ni IDs duplicados, y
coincidir exactamente en los IDs de producto, variantes, SKU, precio
y disponibilidad. Una inconsistencia obliga a dejar la categoría
`PARTIAL`. Los recorridos usan pausa configurable y no descargan en paralelo.

```powershell
git pull --ff-only origin main

# Pruebas locales de reglas de paginación (sin conectarse al sitio)
python -m unittest discover -s tests -p "test_duero_pagination_audit.py" -v

# Captura y auditoría exhaustiva de las 6 categorías de Duero
& .\scripts\test_duero_mayoreo.ps1 -MaxPages 30 -DelaySeconds 1

# Auditoría secundaria de empaque/tienda sobre el Excel (sin descargar)
& C:\Proyectos\venvs\scraper\Scripts\python.exe .\scripts\audit_duero_phase5.py
```

La captura deja evidencia en el workbook
`output\duero_mayoreo_phase5_test.xlsx` (pestaña
`AuditoriaPaginacion`) y dos informes:
`output\reportes_integracion\duero_paginacion_*.csv` (solicitud por
solicitud) y `duero_paginacion_*.json` (resultados por categoría).
Antes de sobrescribir el Excel, guarda un backup en
`output\checkpoints_validos\before_duero_audit_*.xlsx`.

**Clasificación conservadora de empaques (Duero):** el `title` Shopify
puede mencionar contenidos de la presentación (p. ej. "Cepillo 4 Pzas"),
mientras que `variant.title` determina la cantidad vendida ("1 Pieza",
"Exhibidor de 12 Piezas"). El parser prioriza la variante, reconoce
`PIEZA`, `EXHIBIDOR`, `CAJA` y otras opciones explícitas, y deja
`units_per_package` vacío sin evidencia; jamás presupone una unidad.
La auditoría de workbook señala `RECUPERABLE_DEL_TEXTO` y
`CONFLICTO_CANTIDAD`, sin editar los datos.

**Conteo real de tienda vs API:** además de los dos recorridos de API,
comparar el total público de cada categoría con su página HTML en una
misma ejecución. El script no modifica datos productivos ni acredita
contexto de código postal:

```powershell
python -m unittest discover -s tests -p "test_duero_packaging.py" -v
python -m unittest discover -s tests -p "test_duero_storefront_count.py" -v
& C:\Proyectos\venvs\scraper\Scripts\python.exe .\scripts\audit_duero_storefront.py -h
& C:\Proyectos\venvs\scraper\Scripts\python.exe .\scripts\audit_duero_storefront.py --delay-seconds 1
```

Reportes en `output\reportes_integracion\duero_storefront_*.csv/.json`.
Una diferencia o ausencia de conteo se registra como observación y
**no certifica** la equivalencia del catálogo completo. El sitio indica
que se requiere validar el código postal para comprar; esto es un control
separado del conteo público.

**Límite del alcance:** la comparación `limit=50/250` verifica
exhaustividad y estabilidad de la **API pública Shopify**, pero no
demuestra que la tienda física, el código postal o el conteo visible
del storefront sean equivalentes. Hasta validar esa evidencia y las
unidades por caja, Duero permanece PENDIENTE y el master NO se modifica.

La captura desde la API de catálogo Shopify puede validar productos, variantes,
SKU, precios y disponibilidad publicada, pero **no certifica el contexto de
tienda/código postal**. No se convierte automáticamente `SUCCESS` de
extracción en `COMPLETE` integrable. El integrador conserva a Duero como
`PENDIENTE` mientras `store_context_verified=False`.

Una variante sin `units_per_package` no se presume pieza individual: se
distingue una **CAJA sin unidades** de una presentación no determinada.
No se inventa el precio unitario.

```powershell
# No descarga nada y no cambia el master
& C:\Proyectos\venvs\scraper\Scripts\python.exe .\scripts\audit_duero_phase5.py
```

El comando genera `output\reportes_integracion\auditoria_duero_fase5_*.csv`
con presentaciones no determinadas y `*.json` con detalles por categoría,
verificación de contexto y motivos que impiden integrar. El aviso `FutureWarning`
de concatenación se corrigió sin cambiar el esquema de salida. La certificación
del contexto y la interpretación de empaques requieren evidencia observable;
ningún control debe aprobarse basándose únicamente en la disponibilidad
de `collections/.../products.json`.

## Cierre de Fase 5: Mayorista Duero (catálogo online auditado)

Duero puede integrarse **con aprobación explícita del operador** como
catálogo **ONLINE PÚBLICO**, conservando la distinción entre el precio
publicado y una tarifa específica del código postal:

- Seis categorías: 485 productos HTML/API coincidentes, 659 variantes.
- Doble paginación API (50/250) con páginas terminales vacías.
- 659/659 cantidades de empaque y precios unitarios identificados.
- Disponibilidad API normalizada en `is_available` (1/0).
- El operador constató manualmente `Delivery is available to 59690` y
  que precio/presentación de un producto observado eran correctos.
  **Eso NO certifica los 659 precios ni el inventario para CP 59690.**
- Se mantiene `store_context_verified=False`, `postal_code=None` y
  `store_context_method=public_collection_api_no_postal_code` en las
  filas. El `Resumen` registra `integration_scope`,
  `manual_delivery_cp`, `postal_price_verified=False` y rutas a
  los JSON de auditoría del día.

La opción `-ApproveDueroOnline` **solo se aplica a Fase 5** y obliga a
comprobar los reportes del mismo día en
`output\reportes_integracion\duero_paginacion_*.json`,
`duero_storefront_*.json` y `auditoria_duero_fase5_*.json`.
Sin esa aprobación, continúa el control estricto que impide integrar
Duero cuando falta verificación de tienda/CP.

```powershell
cd C:\Proyectos\scraper
git pull --ff-only origin main

python -m unittest discover -s tests -p "test_duero_online_integration.py" -v

# Primero previsualizar. Duero debe aparecer AGREGADO 659,
# no PENDIENTE, y los tres retailers previos ACTUALIZADOS.
& .\scripts\integrate_complete_retailers.ps1 -Phase 5 -Preview -ApproveDueroOnline

# Solo si el preview es correcto:
& .\scripts\integrate_complete_retailers.ps1 -Phase 5 -ApproveDueroOnline
```

La integración conserva copias inmutables y checkpoint, no recuenta
particiones anteriores, y espera **11,290 filas y 12 retailers** si
el master actual tiene 10,631. La Fase 5 solo se reportará `COMPLETE`
al incorporarse Duero junto con Similares, HEB y Sam's Club.

### Checkpoints aislados por categoría (2026-10-19)

La prueba de Chedraui Polanco fue `COMPLETE` para lavandería
(348 filas) e higiene bucal (293 filas), pero una ejecución intermedia
falló y sobrescribió `chedraui_polanco_test.xlsx`. Las capturas
anteriores no se reconstruyen ni se dan por conservadas automáticamente.

A partir de esta versión, cada ejecución aislada **COMPLETE**
guarda un checkpoint separado por sucursal y categoría en
`output\checkpoints_validos\chedraui_cases\<store_id>\<category_id>_<timestamp>.xlsx`.
Antes de sobrescribir la prueba existente, recupera los casos
`COMPLETE` del workbook previo **solo si sus filas coinciden con
el retailer, tienda, categoría y fecha actual** y validan
SKU, URL, precio requerido, disponibilidad 1/0, contexto y
paginación sin incidencias. Los casos fallidos nunca invalidan
checkpoints previos. Cada checkpoint incluye `Concentrado`,
`Resumen` y `Control` y no es un archivo productivo.

El runner de Fase 1 puede reutilizar estos checkpoints del día
en los casos faltantes, priorizando los casos completos de
`fase1_progress.xlsx` o del master de Fase 1. No reutiliza
checkpoints de otros días, tiendas o categorías.

```powershell
cd C:\Proyectos\scraper
git pull --ff-only origin main
python -m unittest discover -s tests -p "test_chedraui_case_cache.py" -v

# Prueba por categoría, conserva la higiene bucal previa si es válida hoy
& .\scripts\test_chedraui.ps1 -Category lavanderia -Store chedraui-polanco -Headed

# Verifica snapshots que sobreviven a fallos posteriores
Get-ChildItem .\output\checkpoints_validos\chedraui_cases\232\*.xlsx
```

No volver a ejecutar todas las 20 combinaciones solo para recuperar
una categoría con `COMPLETE`; ejecutar las pendientes y luego
la corrida productiva de Fase 1 para integrar con controles.

### Integración parcial de Chedraui por sucursal (2026-10-19)

Los cinco checkpoints COMPLETE de Polanco (232) suman 1,271 filas.
El doble checkpoint de desodorantes se deduplica por tienda/categoría;
no se cuentan seis categorías. Los archivos de otras tiendas no se modifican.

Para integrar una sola sucursal se exige: exactamente cinco categorías
activas; captures de la fecha del master; contrato de tienda y control
visible verificado; disponibilidad AVAILABLE=1/UNAVAILABLE=0; SKU,
URL, precios y total publicados completos; sin errores de paginación.
El master debe existir y apuntar al archivo activo de la fecha.

```powershell
cd C:\Proyectos\scraper
git pull --ff-only origin main
python -m unittest discover -s tests -p "test_integrate_chedraui_store.py" -v
& .\scripts\integrate_chedraui_store.ps1 -Store chedraui-polanco
# Solo después de un preview favorable:
& .\scripts\integrate_chedraui_store.ps1 -Store chedraui-polanco -Commit
```

La publicación crea copia del master y del puntero antes de sobrescribir,
y checkpoint posterior. Conserva todos los demás retailers y sucursales.
La Fase 1 sigue PARTIAL_VALIDATED hasta tener las 20 combinaciones.
La siguiente sucursal es Tepeyac (173) con cinco categorías aisladas.
### Tepeyac: radio correcto, CTA de confirmación sin activar (2026-10-19)

La prueba de lavandería fue COMPLETE (356/356) con checkpoint de la tienda 173.
La prueba siguiente, higiene bucal, llegó a estado CDMX y area Metropolitana,
zona CDMX Noreste y seleccionó el radio chedrauimx0173_PP0173,
pero quedó en pickup_confirmed=False. No había productos y no se creó checkpoint.

Corrección: esperar de forma acotada el botón visible exacto
'Recoger en esta tienda', confirmar exclusivamente si permanece
seleccionado el radio de la sucursal configurada y registrar los intentos
en pickup_confirmation_observations. Se eliminaron los botones genéricos
de confirmación para evitar acciones sobre otra UI. Sin confirmación
real, la prueba sigue rechazándose con STORE_CONTEXT_ERROR.

```powershell
cd C:\Proyectos\scraper
git pull --ff-only origin main
python -m unittest discover -s tests -p "test_chedraui_pickup_confirmation.py" -v
$env:CHEDRAUI_TRACE_STORE = "1"
& .\scripts\test_chedraui.ps1 -Category higiene-bucal -Store chedraui-tepeyac -Headed
Remove-Item Env:\CHEDRAUI_TRACE_STORE
```

Si vuelve a fallar, revisar la sección
'ULTIMOS INTENTOS DE CONFIRMAR RECOLECCION', y las capturas
'06_tienda_seleccionada' y '07_confirmacion_no_lograda'.
Solo ejecutar -Category all cuando higiene bucal alcance COMPLETE.
### Cierre Chedraui de hoy: 19 de octubre de 2026

Al terminar la jornada, Polanco 232 (1,271 filas), Tepeyac 173
(1,306 filas) y Cancún Kabah 255 (1,294 filas) tienen cinco
categorías COMPLETE cada una: **15 casos / 3,871 filas**.
Puebla 76 sigue pendiente: no se certifica ni se completa artificialmente.

El cierre por lotes valida TODOS los 15 checkpoints del 19 de octubre,
verifica totales exactos, disponibilidad 1/0, SKU, URL, precios,
tiendas verificadas, no modifica los otros retailers y conserva
FASE 1 = PARTIAL_VALIDATED. Se escribe el master diario una sola vez.
Requiere un master del 19 ya existente y seleccionado por current_master.json.
No crea un nuevo master si falta. Rechaza cierres de otro día.

```powershell
cd C:\Proyectos\scraper
git pull --ff-only origin main
python -m unittest discover -s tests -p "test_cierre_chedraui_20261019.py" -v
& .\scripts\cierre_chedraui_20261019.ps1
# Solamente después de confirmar '15/20' y '3871' en el preview:
& .\scripts\cierre_chedraui_20261019.ps1 -Commit
```

Genera respaldo anterior y checkpoint posterior. Verificar el mensaje
INTEGRACIÓN COMPLETADA antes de afirmar que el master cambió.
Las capturas de Puebla realizadas al día siguiente son de otra fecha:
se deben mantener fechadas y no mezclarse sin definir un corte nuevo.
### Chedraui: 20/20 pruebas validadas en dos fechas (2026-10-20)

Se completaron 4 sucursales x 5 categorías, con 20 ejecuciones aisladas
en estado COMPLETE, cobertura de productos publicada 100%, SKU, URL,
precios requeridos y tienda visible verificada mediante el contrato
`chedraui-directory-ui-v3`. Casos aprobados:

| Sucursal | ID | Fecha de captura | Categorías | Filas | Precios requeridos |
|---|---:|---|---:|---:|---:|
| Polanco | 232 | 2026-10-19 | 5 | 1,271 | 1,251 |
| Tepeyac | 173 | 2026-10-19 | 5 | 1,306 | 1,282 |
| Cancún Kabah | 255 | 2026-10-19 | 5 | 1,294 | 1,260 |
| Puebla Centro Sur | 76 | 2026-10-20 | 5 | 1,268 | 1,249 |
| **Total** | | **Dos fechas** | **20** | **5,139** | **5,042** |

Las cifras se guardaron en el manifiesto versionado
`config/chedraui/validated_runs_20261019_20261020.json`.
Los checkpoints `.xlsx` originales siguen en `output/checkpoints_validos/chedraui_cases/`
de la computadora que ejecutó Playwright: no están incluidos en GitHub.
El manifiesto no sustituye esos archivos ni prueba que el master esté actualizado.

**Regla crítica:** las primeras 15 capturas son del 19 de octubre y
las últimas 5 del 20 de octubre. El integrador diario no debe marcar
todas las 20 como si fueran de una misma fecha. Se añadió una opción
de exportación histórica verificable, separada del master productivo.

```powershell
cd C:\Proyectos\scraper
git pull --ff-only origin main
python -m unittest discover -s tests -p "test_export_chedraui_historico.py" -v

# 1. Verificar 20/20 checkpoints contra su fecha, tienda y totales
& .\scripts\export_chedraui_historico.ps1

# 2. Crear solo el histórico independiente, nunca el master diario
& .\scripts\export_chedraui_historico.ps1 -Commit
```

Salida: `output/historicos/chedraui_validado_2026-10-19_a_2026-10-20.xlsx`
(hojas `Concentrado`, `Resumen`, `Retailers` y `Control`).
El comando rechaza faltantes, duplicados, cambios de fecha, disponibilidad
inconsistente o totales de productos/precios alterados; el archivo histórico
es inmutable si ya existe. No modifica
`output/concentrado_productivo_total_YYYY-MM-DD.xlsx` ni
`output/checkpoints_validos/current_master.json`.

**Para una captura productiva de Chedraui completa en un día nuevo**,
se deben ejecutar sus 20 combinaciones ese mismo día o definir
expresamente un corte multifecha distinto del master diario.
### Chedraui: política de cobertura MVP >=90% (confirmada 2026-10-21)

La política productiva del MVP acepta **cobertura >=90%** del catálogo
publicado, mientras que **100% sigue siendo el objetivo ideal, no obligatorio**.
Además debe cumplirse que:
- SKU y URL estén completos en todos los productos efectivamente capturados.
- El 100% de los precios obligatorios de esos productos esté presente.
- La disponibilidad de cada fila sea válida (`1` disponible / `0` no disponible).
- La sucursal esté verificada visualmente; sin huecos de paginación ni errores de precios.
- El conteo capturado no supere el total publicado y la cobertura declarada
  corresponda a `products / target_products`.

**Caso real:** Polanco (232), higiene bucal el 2026-10-21: **297/299 = 99.33%**.
Cumple el umbral y puede marcarse `COMPLETE` si pasa todos los demás controles.
Los dos productos restantes se reconocen como brecha de cobertura; no
se inventan SKU ni se declara cobertura del 100%. El checkpoint aislado
de esa captura sigue en disco y vuelve a ser elegible bajo la validación
corregida. El concentrado productivo no cambia automáticamente.

Los scripts `scripts/test_chedraui.py`, `scripts/chedraui_case_cache.py` y
`scripts/integrate_chedraui_store.py` usan la misma política >=90%;
un `target_products` ausente, cobertura inferior a 90%, superior a 100%
o discrepancia en los valores de cobertura se rechaza.

```powershell
cd C:\Proyectos\scraper
git pull --ff-only origin main
python -m unittest discover -s tests -p "test_chedraui_strict_coverage.py" -v
python -m unittest discover -s tests -p "test_chedraui_case_cache.py" -v
python -m unittest discover -s tests -p "test_integrate_chedraui_store.py" -v
```

No es necesario repetir la captura 297/299 solo por no llegar a 100%;
la brecha queda registrada para seguimiento y eventual recuperación.
### Chedraui: reintentos automáticos por categoría (2026-10-21)

En la captura aislada `scripts/test_chedraui.ps1` cada categoría dispone de
**un intento inicial más 2 reintentos**, configurables. El runner intenta
recuperar estos errores observados en Polanco:

- `STORE_CONTEXT_ERROR`: vuelve a iniciar el scraper/Chrome y ejecuta
  nuevamente la selección visible de estado, ciudad y sucursal exacta,
  conservando el contrato de verificación (sin asumir que el perfil es prueba).
- `CATALOG_COUNT_OVERFLOW`: si se capturan más filas únicas que productos
  publicados (como 386/384 en desodorantes), repite la categoría desde cero.
  Nunca recorta productos ni modifica el objetivo publicado para forzar éxito.

**Seguridad y consistencia:** no mezcla filas de intentos previos, no reintenta
bloqueos `BLOCKED` ni excepciones ajenas, no cambia el umbral aceptable
de **90%**; mantiene todos los demás controles de precios, SKU, URL,
disponibilidad 1/0 y sucursal verificada. Sólo se guarda un checkpoint
independiente si el último resultado es `COMPLETE` y pasa `persist_case`.
En caso de que los 3 intentos sigan sin verificar la tienda, permanece
`STORE_CONTEXT_ERROR` y la secuencia de `all` se detiene de forma segura.

Por cada intento fallido se conserva JSON con metadatos locales en
`diagnostics/chedraui_retries/<store_id>/<category_id>/`, junto con
archivos de diagnóstico disponibles del intento. La hoja `Resumen`
del test incluye `attempts` y `retry_history`.

```powershell
cd C:\Proyectos\scraper
git pull --ff-only origin main
python -m unittest discover -s tests -p "test_chedraui_category_retries.py" -v

# Sólo las categorías pendientes de Polanco, usando 2 reintentos automáticos
& .\scripts\test_chedraui.ps1 -Category respiratorios -Store chedraui-polanco -Headed
& .\scripts\test_chedraui.ps1 -Category desodorantes-y-antitranspirantes -Store chedraui-polanco -Headed

# Personalizable: 3 reintentos extra y pausa de 15 segundos
& .\scripts\test_chedraui.ps1 -Category all -Store chedraui-polanco -StoreContextRetries 3 -StoreContextRetryDelaySeconds 15 -CategoryDelaySeconds 10 -Headed
```
## Chedraui: diagnóstico de navegación visible y cierre Fase 1

### Fallo intermitente `location_button_opened=False` (2026-10-19)

En la misma sesión, Polanco Lavandería logró 348/348 y después una prueba
`all` falló *antes de abrir el directorio*. Esto **no** demuestra que
la selección Estado/Ciudad/Tienda esté mal: el botón de ubicación
todavía no fue abierto. Se agregaron hasta 10 búsquedas breves mientras
se hidrata el encabezado VTEX, alternativas de control visible y
metadatos de selector/errores por intento. No se da por verificado el
contexto si el localizador no se abre.

La prueba aislada ahora crea un respaldo de su anterior workbook
`output\checkpoints_validos\before_chedraui_<sucursal>_*.xlsx`
antes de repetir la descarga. No restaura capturas sobrescritas
en ejecuciones anteriores a este cambio.

```powershell
cd C:\Proyectos\scraper
git pull --ff-only origin main
python -m unittest discover -s tests -p "test_chedraui_locator_hydration.py" -v

$env:CHEDRAUI_TRACE_STORE = "1"
& .\scripts\test_chedraui.ps1 -Category higiene-bucal -Store chedraui-polanco -Headed
Remove-Item Env:\CHEDRAUI_TRACE_STORE
```

Si vuelve a fallar, inspeccionar
`diagnostics\chedraui_run_meta.json`:
`location_locator_attempts`, `location_button_matches`,
`location_locator_final_url`, y la captura
`diagnostics\chedraui_store_steps\*01_locator_abierto.png`.
**No ejecutar la prueba `-Category all` hasta tener éxito estable
en una categoría aislada.**



La selección de tienda debe seguir la UI pública de forma secuencial:
botón de ubicación visible -> `Recoger en tienda` -> `Directorio de tiendas`
-> Estado -> Ciudad/Zona -> tarjeta/radio de la sucursal exacta ->
`Recoger en esta tienda`/confirmación -> texto de sucursal visible
en el encabezado. Se valida el `store_id` y el contrato
`chedraui-directory-ui-v3` antes de extraer precios; no se altera
manualmente la región VTEX ni se omite un paso fallido.

Auditar los **20 casos exigidos** (4 sucursales x 5 categorías)
sin modificar el master:

```powershell
cd C:\Proyectos\scraper
git pull --ff-only origin main
python -m unittest discover -s tests -p "test_audit_chedraui_phase1.py" -v
& C:\Proyectos\venvs\scraper\Scripts\python.exe .\scripts\audit_chedraui_phase1.py
```

Para depurar el recorrido real de Polanco y compararlo con capturas
manuales, activar capturas opt-in, ejecutar **una sola categoría**
y apagar el modo de traza:

```powershell
$env:CHEDRAUI_TRACE_STORE = "1"
& .\scripts\test_chedraui.ps1 -Category lavanderia -Store chedraui-polanco -Headed
Remove-Item Env:\CHEDRAUI_TRACE_STORE
```

Se guardan imágenes sucesivas `01_locator_abierto` hasta
`08_contexto_verificado` (o `08_contexto_no_verificado`) en
`diagnostics\chedraui_store_steps\`, con la sucursal en el nombre.
Las capturas son datos locales de diagnóstico y **no** se suben al
repositorio. Si aparece CAPTCHA, bloqueo o desafío de identidad,
se detiene la prueba sin intentar evadirlo.

Las pruebas aisladas no integran ni certifican un retailer completo.
Para completar Fase 1, Chedraui exige 20 combinaciones validadas;
la corrida productiva reutiliza los casos válidos del **día**
almacenados en `output\fase1_progress.xlsx`. El integrador sólo
actualiza la partición Chedraui cuando toda la matriz ha pasado los
controles de tienda, SKU, precios y paginación.

## Auditoría integral del master: fases y retailers reales

La lista de retailers esperados está en
`scripts/integrate_complete_retailers.py`, `EXPECTED`. Para ver
**qué está realmente integrado** y no confundir configuración con
producción, ejecutar:

```powershell
git pull --ff-only origin main
python -m unittest discover -s tests -p "test_audit_master_phases.py" -v
& C:\Proyectos\venvs\scraper\Scripts\python.exe .\scripts\audit_master_phases.py --categories
```

Este último comando abre el master señalado por
`output\checkpoints_validos\current_master.json` (o el master del día
como fallback), lee las pestañas `Concentrado`, `Resumen` y
`Control` y presenta:

- Todas las fases, con el total efectivo de filas, retailers
  integrados, casos completos y estatus calculado/control.
- Los 13 retailers esperados (incluido `PENDIENTE` si no hay datos),
  filas reales por retailer, casos integrados/esperados, SKU, precios,
  URLs, disponibilidad `1/0` y contexto de tienda.
- El detalle por categoría/caso con `--categories`.
- Inconsistencias entre conteos reportados y capturas o entre
  `Control` y el estado recalculado.

Deja el CSV `auditoria_master_fases_*.csv` y el JSON homónimo en
`output\reportes_integracion`. **No modifica el master, puntero,
fuentes, checkpoints ni filas.** El total esperado tras integrar a
Duero son 11,290 filas y 12 retailers, pero **solo la lectura del
master productivo confirma su integración y Fase 5 `COMPLETE`**.

## Integración por retailer: COMPLETE / PARTIAL

**Regla operativa (19 de octubre de 2026):** no esperar a que todos los retailers de una fase estén completos. Cada retailer se valida por separado. Si todas sus categorías/casos están en `COMPLETE`, pasa los gates de calidad y corresponde a la fecha del master, se agrega o actualiza **únicamente su partición**. Si está `PARTIAL`, incompleto o bloqueado, queda **PENDIENTE** para revisión al cierre de la fase, sin bloquear los demás.

El master se guarda atómicamente, se genera un checkpoint y se respalda el archivo anterior. No se mezclan fechas; las reintegraciones reemplazan la partición del retailer sin duplicarla.

```powershell
# Vista previa sin modificar el master
& .\scripts\integrate_complete_retailers.ps1 -Phase 4 -Preview

# Integración real de retailers completos
& .\scripts\integrate_complete_retailers.ps1 -Phase 4

# Aplica también a las demás fases
& .\scripts\integrate_complete_retailers.ps1 -Phase 1
& .\scripts\integrate_complete_retailers.ps1 -Phase 2
& .\scripts\integrate_complete_retailers.ps1 -Phase 3
& .\scripts\integrate_complete_retailers.ps1 -Phase 5
```

Los runners de las Fases 1, 2 y 3 invocan la integración incremental al finalizar, incluso si otros retailers quedaron parciales. En Fases 2 y 3, `-SkipIntegration` permite omitirla. Para Fases 4 y 5 se usa el comando al finalizar los tests de retailers.

**Lista de retailers agregados:** se imprime `AGREGADO`, `ACTUALIZADO` o `PENDIENTE`, con productos y motivo. También se guarda CSV y JSON en `output\reportes_integracion\retailers_faseN_YYYYMMDD_HHMMSS_ffffff.*`.

`AGREGADO` es nuevo en el master; `ACTUALIZADO` reemplaza la partición ya existente del mismo día. `PENDIENTE` no modifica los datos de ese retailer. La Fase 1 puede quedar `PARTIAL_VALIDATED` mientras se integran las fases posteriores.

## Producción por fases — recomendada para servidor de 4 GB

Para reducir consumo de memoria, la operación productiva se ejecuta y consolida por checkpoints:

```text
Fase 1 -> validar -> integrar al master -> liberar recursos
Fase 2 -> validar -> integrar al master -> liberar recursos
Fase 3 -> Bodega Aurrera + Walmart
Fase 4 -> Farmacias San Pablo + Farmacias Benavides
Fase 5 -> Farmacias Similares + HEB
```

Integración automática:

- `run_phase1_production.ps1`: si Fase 1 pasa todos sus gates, se integra automáticamente al master diario.
- `run_phase2_production.ps1`: si Fase 2 pasa todos sus gates, se integra automáticamente al mismo master diario.
- Si una fase queda `PARTIAL` o falla, el master no se modifica.
- El integrador vuelve a validar el workbook antes de escribir el master, por lo que la integración mantiene una segunda capa de control.

Los comandos manuales se conservan para recuperación o auditoría:

```powershell
& .\scripts\integrate_validated_phase.ps1 -Phase 1
& .\scripts\integrate_validated_phase.ps1 -Phase 2
& .\scripts\integrate_validated_phase.ps1 -Phase 3
& .\scripts\integrate_validated_phase.ps1 -Phase 5 -Retailer "Farmacias Similares"
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

La Fase 1 inicia una nueva construcción del master. La Fase 2 sólo se incorpora cuando el master ya contiene Fase 1 válida. La Fase 3 sólo se incorpora cuando Fase 1 y Fase 2 ya están validadas en ese master. Una fase reejecutada reemplaza su propia partición y no duplica filas.

## Corridas diarias

El scraping es reiterativo y cada día genera una captura independiente.

Regla:
- el método validado de cada retailer se conserva
- los datos de productos/precios se vuelven a capturar cada día
- nunca se reutilizan datos de una fecha anterior como si fueran de la fecha actual
- nunca se mezclan fases o componentes de fechas distintas

Cada Fase 1 válida abre una fecha de captura. El master diario usa:

```text
output\concentrado_productivo_total_YYYY-MM-DD.xlsx
```

Los componentes de Fase 3 se guardan por fecha:

```text
output\validated_phase3_components\YYYY-MM-DD\
```

Ejemplo:

```text
output\validated_phase3_components\2026-10-12\
  bodega_aurrera__cuidado-bucal.xlsx
  bodega_aurrera__cuidado-de-la-ropa.xlsx
  walmart__cuidado-bucal.xlsx
  walmart__cuidado-de-la-ropa.xlsx
  walmart__depilacion-y-rasurado.xlsx
```

El integrador compara la fecha de la fase contra la fecha del master activo y rechaza cualquier mezcla entre días.

## Consolidación incremental por componentes validados

Los componentes NO constituyen una fase nueva ni cambian la arquitectura oficial. Son únicamente checkpoints internos de **Fase 3** para no repetir una categoría exitosa dentro de la misma corrida diaria. El flujo oficial sigue siendo `Fase 1 -> Fase 2 -> Fase 3 -> master diario`.

Para Fase 3 se evita volver a scrapear categorías que ya pasaron validación.

Flujo:

```text
test de categoría
→ revisar resultado live
→ promover categoría aprobada
→ componente interno congelado en output\validated_phase3_components\YYYY-MM-DD
→ repetir sólo categorías pendientes
→ construir concentrado_fase3.xlsx desde 5 componentes
→ integrar -Phase 3 al master fechado
```

Promover una categoría ya aprobada:

```powershell
& .\scripts\promote_phase3_component.ps1 `
    -Source ".\output\bodega_aurrera_test.xlsx" `
    -Retailer "Bodega Aurrera" `
    -CategoryId "cuidado-de-la-ropa"
```

Los componentes estables quedan en una carpeta diaria:

```text
output\validated_phase3_components\YYYY-MM-DD\
```

y cada promoción genera además un checkpoint inmutable en:

```text
output\checkpoints_validos\phase3_components\YYYY-MM-DD\
```

Cuando existan los 5 componentes esperados:

```powershell
& .\scripts\build_phase3_from_validated_components.ps1
```

Este comando NO navega retailers ni vuelve a scrapear. Sólo construye:

```text
output\concentrado_fase3.xlsx
```

con:
- Bodega Aurrera / cuidado-bucal
- Bodega Aurrera / cuidado-de-la-ropa
- Bodega Aurrera / desodorantes
- Walmart / cuidado-bucal
- Walmart / cuidado-de-la-ropa
- Walmart / depilacion-y-rasurado

Después se integra al master activo:

```powershell
& .\scripts\integrate_validated_phase.ps1 -Phase 3
```

El integrador exige que Fase 1 y Fase 2 ya estén en el master y reemplaza únicamente la partición de Fase 3, sin modificar las fases anteriores.

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
→ validar 6 categorías
→ publicar output\concentrado_fase3.xlsx
```

### Bodega Aurrera — conservación de éxitos y consolidación

Categorías activas: `cuidado-bucal`, `cuidado-de-la-ropa` y `desodorantes`.

Para probar la nueva categoría de desodorantes **sin integrar todavía al master**:

```powershell
& .\scripts\test_bodega_aurrera.ps1 -Category desodorantes -RetryFailedAtEnd 2
```

La prueba aislada preserva en `output\bodega_aurrera_test.xlsx` las demás categorías `SUCCESS` capturadas en la misma fecha; solo actualiza desodorantes. El archivo Excel se escribe de forma atómica para proteger la versión previa si falla la escritura. El banner patrocinado no cuenta como ficha y no se pulsa su botón `Ver más`. La paginación se controla por el componente numérico visible, verificando que cada página avanza hasta que termina.

En la corrida completa de Bodega:

1. cada categoría se ejecuta una sola vez en la primera pasada;
2. cada resultado se guarda inmediatamente en `output\bodega_aurrera_test.xlsx`;
3. las categorías `SUCCESS` se conservan y no vuelven a descargarse;
4. las categorías fallidas se acumulan y se reintentan únicamente al final;
5. por defecto se hace 1 reintento final, configurable con `-RetryFailedAtEnd`;
6. si las 3 categorías terminan válidas, el wrapper agrega automáticamente la partición de Bodega al master diario mediante el mismo integrador de fases;
7. esa integración parcial no marca Fase 3 como completa: `completed_phases` permanece en `1|2` hasta que toda Fase 3 quede cerrada.

Integración manual de una muestra Bodega ya validada, sin volver a scrapear:

```powershell
& .\scripts\integrate_validated_phase.ps1 `
    -Phase 3 `
    -Retailer "Bodega Aurrera"
```

Bodega Aurrera y Walmart reutilizan el mismo Chrome abierto manualmente mediante remote debugging. Deben quedar abiertas una pestaña de Bodega Aurrera y otra de Walmart. En Walmart debe estar seleccionado SC Toreo, tienda 2344, CP 11220. El runner no abre ni cierra Chrome y no resuelve verificaciones de identidad.

Bodega Aurrera usa las rutas `/browse/` directas de Pasta Dental, Enjuagues Bucales, Detergente en Polvo, Detergente Líquido y Desodorantes. En Bodega se ignoran banners patrocinados y nunca se pulsa el `Ver más` de esos bloques: el scraper hace scroll hasta el paginador numérico `1 2 3 … N >` y lo recorre una página a la vez. Tanto Bodega como Walmart trabajan secuencialmente, con pausas de 2.5 s entre páginas y 4 s entre categorías. Si el sitio publica un target confiable, una cobertura de **>=90%** es válida; sin target se exige demostrar fin de paginación. La completitud de SKU/precio/URL de las filas extraídas sigue siendo 100%.

Cuando Fase 3 pase sus gates:

```powershell
& .\scripts\integrate_validated_phase.ps1 -Phase 3
```

Esto agrega exclusivamente Bodega Aurrera + Walmart al concentrado fechado activo sin volver a ejecutar Fase 1 o Fase 2.

## Fase 4 — Farmacias San Pablo + Farmacias Benavides

### Ejecución integral del día

Con la pestaña de Farmacias San Pablo abierta en Chrome y CDP habilitado:

```powershell
& .\scripts\run_phase4_production.ps1
```

El runner captura secuencialmente las 4 categorías de San Pablo mediante el Chrome existente y las 3 categorías de Benavides mediante Playwright. Reutiliza los casos correctos del día, ejecuta **una única integración incremental de Fase 4** (solo retailers completos y de la misma fecha) y termina con `audit_master_phases.py`, mostrando estados de todas las fases y retailers. Si uno de los retailers queda pendiente, el otro aún puede integrarse. La salida y el detalle se guardan en `diagnostics\phase4_production\` y `output\reportes_integracion\`.

Para capturar y auditar sin integrar al master: `& .\scripts\run_phase4_production.ps1 -SkipIntegration`.

Estado: **VALIDADO**

Fase 4 contiene dos retailers y siete categorías:

```text
Farmacias San Pablo   4 categorías   232 productos
Farmacias Benavides   3 categorías   800 productos
Total Fase 4          7 categorías  1032 productos
```

La integración es incremental por retailer para no volver a scrapear ni reemplazar la otra partición.

San Pablo:

```powershell
& .\scripts\integrate_validated_phase.ps1 -Phase 4 -Retailer "Farmacias San Pablo"
```

Benavides:

```powershell
& .\scripts\integrate_validated_phase.ps1 -Phase 4 -Retailer "Farmacias Benavides"
```

Fase 4 queda marcada `COMPLETE` únicamente cuando ambos retailers están presentes en el master de la misma fecha.

Última integración live — 13 de octubre de 2026:

```text
Fases completadas : 1|2|3|4
Estado Fase 3     : COMPLETE
Estado Fase 4     : COMPLETE
Retailers Fase 4  : Farmacias Benavides | Farmacias San Pablo
Filas acumuladas  : 10304
Categorías        : 32
Retailers         : 9
Master            : output\concentrado_productivo_total_2026-10-13.xlsx
```


### Farmacias San Pablo

Fuente:

```text
output\farmacias_san_pablo_test.xlsx
```

Validación live del 13 de octubre de 2026:

```text
descongestionantes   52 / 52
preservativos        49 / 49
enjuagues-bucales    46 / 46
pastas-dentales      85 / 85
Total               232 / 232
```

Patrón validado: Chrome existente + CDP + captura OCC `search-sponsored` después de `requestfinished`.

### Farmacias Benavides

Fuente:

```text
output\benavides_test.xlsx
```

Validación live del 13 de octubre de 2026:

```text
medicamentos-respiratorios   456 / 456
bienestar-sexual             170 / 170
higiene-bucal                174 / 174
Total                        800 / 800
```

Completitud:
- SKU 800/800
- precio 800/800
- URL 800/800
- disponibilidad 799/800
- cobertura de catálogo 100%

Respiratorios registró 2 `price_order_errors`, por lo que 454/456 filas (99.56%) conservan orden de precio consistente. Esto supera el umbral de muestra válida >=90% y los dos casos permanecen visibles para auditoría.

Evidencia:
- `docs/validation_results/farmacias_san_pablo_validated_2026-10-13.json`
- `docs/validation_results/benavides_validated_2026-10-13_800.json`

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

Fase 1 ahora contiene **15 retailer-categorías únicas**: 6 de Farmacias del Ahorro, 4 de Ibarra Mayoreo y 5 de Chedraui. Debido a que Chedraui se ejecuta en 4 tiendas, la Fase 1 completa tiene **30 casos productivos**: 6 Ahorro + 4 Ibarra + 20 Chedraui. Las categorías `anti-acne` y `desodorantes` de Farmacias del Ahorro, así como las tres nuevas categorías de Chedraui, fueron validadas live el 14 de octubre de 2026.

Importante: el master histórico `concentrado_productivo_total_2026-10-13.xlsx` conserva 32 categorías porque fue generado antes de estas altas. Las dos nuevas categorías entran a partir de la siguiente corrida diaria fresca y no se retroinsertan en una fecha anterior.


La primera corrida productiva está limitada a los retailers estables:

- Farmacias del Ahorro: todas las categorías activas.
- Ibarra Mayoreo: todas las categorías activas, conservando presentación, unidades por empaque y precio por pieza. Cuando existe CAJA, el precio unitario se calcula como precio de caja / artículos por caja; si sólo existe presentación individual, se registra como single item.
- Chedraui: 5 categorías activas en 4 tiendas validadas: Polanco (232), Tepeyac (173), Cancún Kabah (255) y Puebla Centro Sur (76).

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

- **Farmacias Guadalajara**: cobertura por categoría >= 90%, nombre/SKU/precio/URL >=90% y ejecución exitosa.
- **Soriana**: recorrido de toda la paginación detectada, productos > 0 y SKU/precio/URL >=90%.

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

## Política global de reintentos y umbral de calidad

Regla vigente para **Fases 1, 2, 3, 4 y 5**:

- el umbral común de aceptación para cobertura y completitud cuantitativa es **>= 90%**;
- una categoría/caso que ya terminó válido **en la fecha actual** se conserva como checkpoint y **no se vuelve a scrapear**;
- si una categoría falla, queda pendiente y en el siguiente intento se ejecuta **sólo esa categoría/caso fallido**;
- los checkpoints de otro día no se reutilizan para una captura nueva;
- los gates estructurales siguen siendo obligatorios: contexto de tienda cuando aplique, ausencia de bloqueo, paginación demostrada, retailer/categoría correctos y consistencia interna del workbook;
- errores de orden/validación de precio se evalúan por tasa válida: al menos 90% de las filas deben conservar orden de precio consistente.

Implementación por fase:

- **Fase 1:** usa `output/fase1_progress.xlsx` como checkpoint parcial del día. Farmacias del Ahorro, Ibarra y cada combinación tienda-categoría de Chedraui se reanudan individualmente.
- **Fase 2:** `farmacias_guadalajara_phase2_test.xlsx` y `soriana_manual_phase2_test.xlsx` conservan categorías exitosas del día; el runner productivo ya no borra estos checkpoints.
- **Fase 3:** Bodega Aurrera y Walmart reutilizan categorías válidas del día; el runner productivo ya no elimina sus workbooks antes de ejecutarse.
- **Fase 4:** San Pablo y Benavides conservan categorías válidas del día y sólo ejecutan las pendientes.
- **Fase 5:** Farmacias Similares (2 categorías), HEB (2), Sam's Club (3) y Mayorista Duero (6) se evalúan individualmente mediante `integrate_complete_retailers.ps1 -Phase 5`. Se integra exclusivamente cada retailer `COMPLETE` del día; los parciales no bloquean los completos. Se conserva el umbral de calidad >=90% donde corresponde, junto con gates específicos de tienda, paginación y promociones. La Comer permanece fuera de Fase 5.

El umbral común está centralizado en:

```text
scraper/quality_rules.py
MIN_SUCCESS_RATE = 0.90
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

Tiendas productivas validadas:

```text
chedraui-polanco             store_id 232   CDMX
chedraui-tepeyac             store_id 173   CDMX
chedraui-cancun-kabah        store_id 255   Quintana Roo
chedraui-puebla-centro-sur   store_id 76    Puebla
```

Categorías productivas activas:

```text
higiene-bucal
lavanderia
desodorantes-y-antitranspirantes
depilacion
respiratorios
```

El 14 de octubre de 2026 se completó la certificación exhaustiva de **4 tiendas × 5 categorías = 20 combinaciones**, todas con exit code 0. El gate productivo exige contexto de tienda verificado, cobertura >=90%, SKU y URL >=90%, precio requerido >=90%, cero páginas estructuradas faltantes y cero huecos internos de paginación. El orden de precio debe ser válido en >=90% de las filas.

Prueba completa aislada:

```powershell
& .\scripts\test_chedraui.ps1 -Headed
```

Sin `-Category`, el runner ejecuta las cinco categorías productivas. Para una sola categoría puede usarse cualquiera de los cinco IDs activos.

### Expansión multitienda Chedraui

Estado: **VALIDADA LIVE / PRODUCTIVA**

Las cuatro tiendas productivas validadas son:

```text
chedraui-tepeyac
store_id: 173
CP: 07850
Ciudad: Gustavo A. Madero, CDMX

chedraui-cancun-kabah
store_id: 255
CP: 77501
Ciudad: Cancún, Quintana Roo

chedraui-puebla-centro-sur
store_id: 76
CP: 72490
Ciudad: Puebla, Puebla
```

El scraper usa un único flujo de selección por directorio VTEX para las cuatro tiendas. Las diferencias de estado, zona de directorio, store_id y código postal se resuelven por configuración y metadata live, no mediante ramas específicas por tienda.

Prueba aislada de una tienda/categoría:

```powershell
& .\scripts\test_chedraui.ps1 -Store chedraui-tepeyac -Category higiene-bucal -Headed
```

Las cuatro tiendas forman parte de la corrida productiva de Fase 1. El runner usa el mismo flujo de directorio VTEX para todas y mantiene perfiles separados por tienda.


### Nuevas categorías Chedraui

Estado: **VALIDADAS LIVE / PRODUCTIVAS**

Las cinco categorías productivas actuales de Chedraui están en:

```text
config\chedraui\categories.yaml
```

Las tres categorías promovidas son:

```text
desodorantes-y-antitranspirantes
Cuidado e higiene personal > Cuidado corporal > Desodorantes y antitranspirantes

depilacion
Belleza > Afeitado y depilación > Depilación

respiratorios
Farmacia > Medicamentos > Respiratorios
```

Probar las cinco categorías productivas en una tienda:

```powershell
& .\scripts\test_chedraui.ps1 -Store chedraui-tepeyac -Category all -Headed
```

Probar una sola combinación:

```powershell
& .\scripts\test_chedraui.ps1 -Store chedraui-cancun-kabah -Category respiratorios -Headed
```

La matriz de expansión de las tres categorías nuevas cerró 12/12 combinaciones válidas. Después se ejecutó una certificación completa de las cinco categorías en las cuatro tiendas, cerrando 20/20 casos exitosos.



### Validación multitienda con pausa

Para validar las categorías candidatas en las cuatro tiendas sin lanzar tráfico en paralelo:

```powershell
& .\scripts\test_chedraui_multistore.ps1 -Category all -Headed
```

El runner ejecuta secuencialmente:

```text
Polanco
↓ 60 s
Tepeyac
↓ 60 s
Cancún Kabah
↓ 60 s
Puebla Centro Sur
```

El delay se puede ajustar:

```powershell
& .\scripts\test_chedraui_multistore.ps1 `
    -Category all `
    -StoreDelaySeconds 60 `
    -Headed
```

No hay paralelismo entre tiendas y cada tienda conserva su propio perfil/output. Una tienda con observaciones no impide probar las siguientes. El consolidado productivo no se modifica.


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


### Nueva categoría validada — Anti-Acné

Estado: **VALIDADA LIVE / ACTIVA PARA LA PRÓXIMA FASE 1**

Jerarquía:

```text
Cuidado de la Piel
└── DERMA
    └── Cuidado Facial
        └── Anti-Acné
```

URL:

```text
https://www.fahorro.com/derma/cuidado-facial/anti-acne.html
```

ID interno:

```text
anti-acne
```

Prueba aislada:

```powershell
& .\scripts\test_farmacias_del_ahorro.ps1 -Category anti-acne
```

Gate candidato:
- cobertura del target publicado >= 90%;
- SKU >= 90%;
- precio actual >= 90%;
- URL >= 90%.

Validación live del 14 de octubre de 2026: 119/119 productos, cobertura 100%, SKU/precio/URL 119/119. Esta categoría ya forma parte de la definición de Fase 1 para la siguiente corrida fresca.



### Nueva categoría validada — Desodorantes

Estado: **VALIDADA LIVE / ACTIVA PARA LA PRÓXIMA FASE 1**

Jerarquía:

```text
Higiene y Cuidado Personal
└── Desodorantes
    ├── Aerosol
    ├── Barra
    ├── Clinical
    ├── Gel
    └── Roll-On
```

URL:

```text
https://www.fahorro.com/cuidado-personal/desodorantes.html
```

ID interno:

```text
desodorantes
```

Prueba aislada:

```powershell
& .\scripts\test_farmacias_del_ahorro.ps1 -Category desodorantes
```

Gate candidato:
- cobertura del target publicado >= 90%;
- SKU >= 90%;
- precio actual >= 90%;
- URL >= 90%.

Validación live del 14 de octubre de 2026: 184/186 productos, cobertura 98.92%, SKU/precio/URL 184/184. Esta categoría ya forma parte de la definición de Fase 1 para la siguiente corrida fresca.


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

La implementación validada usa **Chrome ya abierto por el usuario + CDP**. El scraper navega el storefront real y captura la respuesta OCC `search-sponsored` que esa navegación genera, una vez que Chrome marca la solicitud como terminada. No vuelve a ejecutar el endpoint desde un cliente HTTP separado, evitando el 403 de Akamai observado al usar `urllib`.

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

Última validación live en Windows Server — **13 de octubre de 2026**:

```text
descongestionantes   52 / 52   SUCCESS
preservativos        49 / 49   SUCCESS
enjuagues-bucales    46 / 46   SUCCESS
pastas-dentales      85 / 85   SUCCESS
Total               232 / 232
```

Las cuatro categorías usaron `transport=captured_storefront_response`. El gate exige cobertura contra `target_products` **>= 90%** cuando OCC lo publica y precio actual completo en **>= 90%** de las filas extraídas.

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

Evidencia de validación:

```text
docs\validation_results\farmacias_san_pablo_validated_2026-10-13.json
```

Patrón estable: `36c37c60e5daf0d633912e2ded77c04ffaebfa17`. No sustituir el método OCC capturado mientras continúe funcionando; cualquier alternativa debe probarse de forma aislada antes de reemplazarlo.

## Farmacias Benavides

Estado: **IMPLEMENTADO / PENDIENTE DE VALIDACIÓN LIVE**

Categorías candidatas:

```text
medicamentos-respiratorios
bienestar-sexual
higiene-bucal
```

URLs:

```text
https://www.benavides.com.mx/medicamentos-respiratorios
https://www.benavides.com.mx/bienestar-sexual
https://www.benavides.com.mx/cuidado-personal-higiene-bucal
```

El storefront es server-rendered y publica:
- total de artículos por categoría;
- paginación mediante `?p=N`;
- nombre, marca, precio actual, precio regular y promociones en el catálogo;
- SKU explícito en la ficha de producto;
- estado de disponibilidad en la ficha.

Observación web del 13 de octubre de 2026, sólo como referencia y **no como gate fijo**:
- Respiratorios: alrededor de 456 artículos;
- Bienestar sexual: 170 artículos;
- Higiene bucal: alrededor de 175 artículos.

Patrón candidato:
- descubrir catálogo y target desde la categoría;
- recorrer todas las páginas hasta igualar el target;
- visitar cada ficha usando el contexto HTTP de la misma sesión Playwright;
- si la lectura HTTP de ficha falla, usar navegación normal de Chrome como fallback;
- obtener SKU, precio, marca, URL y disponibilidad;
- registrar `is_available=1` cuando el producto está disponible y `0` cuando el sitio lo marca agotado/no disponible;
- conservar `availability_raw` para auditoría.

Gate candidato:
- cobertura de catálogo >= 90% del target publicado;
- SKU >= 90% de las filas extraídas;
- precio actual >= 90%;
- URL >= 90%;
- disponibilidad binaria >= 90%;
- orden de precio consistente en **>= 90%** de las filas (`price_current <= price_regular` cuando ambos existen).

Regla de muestra válida:
- el proyecto considera válida una muestra con cobertura >= 90% del universo publicado;
- no es necesario exigir 100% para aprobar una corrida;
- las métricas de completitud se siguen reportando para auditoría y mejora continua.

Test de una categoría:

```powershell
& .\scripts\test_benavides.ps1 -Category higiene-bucal
```

Test completo:

```powershell
& .\scripts\test_benavides.ps1
```

Salida:

```text
output\benavides_test.xlsx
```

Regla:
- no integrar al master ni asignar una fase productiva hasta observar una corrida live completa exitosa;
- los conteos publicados pueden cambiar día a día y no deben fijarse como constantes.

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

## HEB

Estado: **IMPLEMENTADO / VALIDACIÓN LIVE PARCIAL / FASE 5**

Categorías activas:

```text
higiene-dental
Cuidado personal y belleza > Higiene dental
https://www.heb.com.mx/cuidado-personal-y-belleza/higiene-dental

lavanderia
Lavandería
https://www.heb.com.mx/lavanderia
```

El usuario abre HEB manualmente en Chrome y habilita remote debugging. El scraper se conecta por CDP a esa misma sesión, navega cada categoría en la misma pestaña y recorre el paginador nativo del storefront. La fuente principal validada es el DOM del catálogo; si aparece `productSearchV3`, se usa como enriquecimiento adicional. La disponibilidad se conserva en formato binario `1` disponible / `0` no disponible.

Gate por categoría:
- cobertura de catálogo >= 90%;
- SKU >= 90%;
- precio actual >= 90%;
- URL >= 90%;
- disponibilidad binaria >= 90%;
- orden de precios válido >= 90%;
- evidencia de catálogo por DOM o `productSearchV3`;
- sin bloqueo.

Higiene Dental quedó validada live el 16 de octubre de 2026 con 272/272 productos, cobertura 100%, SKU/precio/URL/disponibilidad completos, 10 páginas y cero bloqueos. Al agregar Lavandería, ese checkpoint del mismo día se conserva y el runner sólo debe ejecutar la nueva categoría pendiente.

La certificación de tienda de HEB se obtiene de la misma sesión Chrome/CDP mediante evidencia explícita del storefront y/o browser storage. El scraper rechaza selectores genéricos y evidencia conflictiva. Para integrar HEB, el 100% de las filas de ambas categorías debe tener `store_context_verified=True`, un método no vacío y una única tienda concreta compartida. Debido a que las muestras 272/272 y 283/283 fueron capturadas antes de este gate, ambas categorías deben ejecutarse una vez más para regenerar las 555 filas con el contexto de tienda certificado.

Test:

```powershell
& .\scripts\test_heb.ps1
```

Salida:

```text
output\heb_phase5_test.xlsx
```

Después de una validación live satisfactoria:

```powershell
& .\scripts\integrate_validated_phase.ps1 -Phase 5 -Retailer "HEB"
```

Fase 5 sólo queda `COMPLETE` cuando **Farmacias Similares, HEB, Sam's Club y Mayorista Duero** están integrados para la misma fecha. Cada retailer completo puede sumarse antes de que los demás terminen.

## La Comer

Implementado pero no activado en el runner principal. **La Comer no forma parte de Fase 5** y permanece pendiente hasta completar una validación live satisfactoria.

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

### Método único de selección de tienda Chedraui

Todas las sucursales Chedraui usan el mismo flujo de storefront VTEX:

```text
Abrir selector de ubicación
→ Recoger en tienda
→ Directorio de tiendas
→ Seleccionar estado
→ Seleccionar ciudad/municipio
→ Seleccionar sucursal exacta
→ Recoger en esta tienda
→ Verificar contexto de tienda
```

No existe una ruta especial por sucursal. Polanco, Tepeyac, Cancún Kabah y Puebla Centro Sur usan la misma función y sólo cambian sus datos de ubicación y el pickup point devuelto por VTEX.


### Mayorista Duero — candidato Fase 5 (6 categorías)

El retailer `dueromayoreo.mayoreoenlinea.mx` se añadió como candidato de extracción independiente. Usa un storefront Shopify, distinto al HTML y reglas de navegación de Ibarra. El objetivo es mantener comparabilidad de precios por empaque, sin inferir unidades no verificadas.

Categorías: `cuidado-bucal`, `desodorantes-y-antitranspirantes`, `cuidado-facial-y-corporal`, `farmacia`, `detergentes`, `cloros-y-blanqueadores`.

```powershell
& .\scripts\test_duero_mayoreo.ps1
```

El test recorre secuencialmente las colecciones por paginación pública Shopify y escribe `output/duero_mayoreo_phase5_test.xlsx` con hojas `Concentrado` y `Resumen`. **No integra automáticamente**. Antes de incorporar Duero a producción deben validarse el acceso real a las colecciones, la cobertura, las presentaciones/cajas, los SKU y el contexto de tienda/código postal; el integrador rechaza resultados sin contexto de tienda certificado.

