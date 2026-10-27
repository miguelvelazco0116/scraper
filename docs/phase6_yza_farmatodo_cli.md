# Fase 6 · Farmacias YZA + Farmatodo (validación por CLI)

## Contrato de la nueva fase

- Farmacias YZA, 2 categorías: medicamentos-respiratorio e higiene-bucal;
  CP obligatorio 11590.
- Farmatodo, 3 categorías: respiratorios, cuidado-bucal e higiene-personal;
  catálogo online sin verificación de código postal.
- Total esperado: 2 retailers y 5 casos.
- La asignación está declarada en config/phase6/retailers.yaml.
  Aún no es integración productiva ni una página de Streamlit.

## Orden acordado

1. Soriana: probar primero rotación 83420 → 44220 → 54030 → 97245
   desde la rama feat/soriana-zip-rotation-20261026.
2. Terminar y cerrar los procesos de Soriana.
3. Cambiar a esta rama de Fase 6 y ejecutar tests unitarios.
4. Diagnóstico de dos categorías YZA y tres Farmatodo.
5. Captura de los cinco casos y auditoría del reporte resultante.
6. Después de certificarlos, desarrollar la integración en master
   y Streamlit con controles de seis fases.

## Preparar el entorno (después de terminar Soriana)

Ejecutar desde C:\Proyectos\scraper en Windows PowerShell con el venv
C:\Proyectos\venvs\scraper\Scripts\Activate.ps1 activo.

    git status --short
    git fetch origin
    git switch --track origin/feat/phase6-yza-farmatodo-cli-20261026

Si la rama ya existe localmente:

    git switch feat/phase6-yza-farmatodo-cli-20261026
    git pull --ff-only

Nunca resetear modificaciones locales para cambiar de rama.

    python -m pytest tests/test_phase6_pilot.py tests/test_yza_pilot.py tests/test_farmatodo_pilot.py -v

## Modo de navegador: primero Chrome automático

**No hace falta abrir Chrome previamente.** Los runners de Fase 6 lanzan
Google Chrome visible mediante Playwright, visitan las URLs configuradas y
cierran solo los navegadores que ellos mismos abrieron. YZA intenta elegir
una dirección sugerida del CP **11590** y solo certifica resultados cuando
ese código se confirma en la página fuera de los campos editables.
Farmatodo captura catálogo online sin CP, sin inferir disponibilidad local.

Si YZA muestra bloqueo o no permite confirmar una dirección, no se fuerza
ninguna captura. Para probar una sesión Chrome abierta previamente:

1. Abrir Google Chrome, habilitar
   chrome://inspect/#remote-debugging y abrir una pestaña de www.yza.mx.
2. Confirmar manualmente en YZA un domicilio válido con CP **11590**.
3. Repetir el piloto con el parámetro `-UseExistingChrome`:

       & .\scripts\run_phase6_pilot.ps1 -Mode probe -Retailer farmacias-yza -UseExistingChrome

El runner localiza el puerto de depuración activo en DevToolsActivePort. Si
esa detección falla, se puede pasar el endpoint real de la máquina con
`-CdpUrl 'http://127.0.0.1:9222'` (solo si Chrome usa ese puerto).
En modo CDP, no se cierra el navegador ni la pestaña existente del operador.

## Prueba A: cinco diagnósticos

    & .\scripts\run_phase6_pilot.ps1 -Mode probe -Retailer all

Dos diagnósticos YZA y tres Farmatodo. Un resultado READY_FOR_CAPTURE
es solo una señal de que pueden intentarse capturas; NO certifica los
cinco catálogos completos. El probe de Farmatodo NO espera cargar toda la
paginación, pero ahora rechaza un diagnóstico sin ningún SKU identificado:
estado NEEDS_SKU_REVIEW. Guardará hasta tres ejemplos públicos de URL e
imagen de los productos con SKU faltante en
diagnostics/farmatodo/probe_<category>_<date>.json.
**No ejecutar capture completo hasta resolver esos faltantes.**

## Prueba B: cinco categorías, captura real

    & .\scripts\run_phase6_pilot.ps1 -Mode capture -Retailer all

La ejecución usa YZA (2 categorías y CP fijo 11590), seguido de
Farmatodo (3 categorías sin CP). Debe verificar fecha, URL, precio,
SKU, categorías, duplicados y contexto por retailer antes de declarar
COMPLETE_PILOT.

Se pueden aislar fallas por retailer con:

    & .\scripts\run_phase6_pilot.ps1 -Mode probe -Retailer farmacias-yza
    & .\scripts\run_phase6_pilot.ps1 -Mode probe -Retailer farmatodo
    & .\scripts\run_phase6_pilot.ps1 -Mode capture -Retailer farmacias-yza
    & .\scripts\run_phase6_pilot.ps1 -Mode capture -Retailer farmatodo

## Archivos de diagnóstico

- output/yza_cp11590_test.xlsx
- output/pilots/farmatodo/catalogo_online.xlsx
- diagnostics/yza/last_run.json
- diagnostics/farmatodo/last_run.json
- diagnostics/phase6/last_run.json

En modo captura el resultado correcto es COMPLETE_PILOT. Los pilotos
no tocan el master de Fases 1–5, su puntero ni Streamlit. Ningún
COMPLETE_PILOT equivale por sí solo a una fase integrada/productiva.

Cuando ambas tiendas sean verificadas en Chrome y sus archivos pasen
los gates, habilitar formalmente Fase 6 en runner, auditor, integrador
y panel mediante una segunda modificación con respaldo del master.


## Evidencia del probe real del 27 de octubre de 2026

La versión anterior, con CDP obligatorio, produjo:
- YZA: 2 errores porque no había pestaña YZA en el Chrome CDP; no se observó
  un bloqueo real ni se certificó CP 11590.
- Farmatodo Respiratorios: 9 tarjetas de 460 publicados, 9 SKU faltantes.
- Farmatodo Cuidado bucal: 6 tarjetas de 169, 6 SKU faltantes.
- Farmatodo Higiene personal: 6 tarjetas de 140, 6 SKU faltantes.

Los 769 artículos publicados NO son un resultado capturado; 21 fueron solo
tarjetas visibles de una muestra y requieren resolver el SKU. La nueva prueba
usa Chrome propio y registra muestras de las imágenes para diagnosticar el
código. No declarar READY_FOR_CAPTURE para Farmatodo con 0 SKU.

### Comandos de revalidación

    git pull --ff-only
    python -m pytest tests/test_phase6_pilot.py tests/test_yza_pilot.py tests/test_farmatodo_pilot.py -v
    & .\scripts\run_phase6_pilot.ps1 -Mode probe -Retailer farmacias-yza
    & .\scripts\run_phase6_pilot.ps1 -Mode probe -Retailer farmatodo

Si hay bloqueo o YZA requiere una dirección aplicada manualmente:

    & .\scripts\run_phase6_pilot.ps1 -Mode probe -Retailer farmacias-yza -UseExistingChrome

Estos cambios pertenecen solo a un PR borrador de Fase 6; master intacto.


## 27/oct — Chrome automático YZA funciona; CP no confirmado

La segunda ejecución real de YZA (Chrome iniciado desde Playwright)
detectó 18 productos por página en ambas categorías. La paginación
mostró 20 páginas para medicamentos-respiratorio y 7 para higiene-bucal.
El estado fue NEEDS_REVIEW porque context_verified=False en ambas.
**No se observó un bloqueo.** Los 18 productos son visibles, no una
captura válida para CP 11590.

La página pública incluye un campo "Digita la dirección y selecciona
una opción de la lista de sugerencias" dentro de la interfaz
"Consultar disponibilidad". Ese diálogo puede ser de disponibilidad
por producto y no demuestra que el código postal se aplique globalmente.

El probe actualizado deja un JSON que explica qué entradas
de dirección están visibles, cuáles están en un diálogo y qué botones
de ubicación aparecen, sin leer ni guardar los domicilios escritos.
Si el CP no es confirmado, también guarda una captura de pantalla
visible de Chrome en diagnostics/yza/postal_ui_<category>_<timestamp>.png.
Si el sitio muestra datos personales, ocultarlos antes de compartir
la captura con terceros.

### Repetir en Chrome automático, solo diagnóstico

    git pull --ff-only
    python -m pytest tests/test_yza_pilot.py tests/test_phase6_pilot.py tests/test_farmatodo_pilot.py -v
    & .\scripts\run_phase6_pilot.ps1 -Mode probe -Retailer farmacias-yza

### Fallback a navegador ya abierto, solo si se necesita

1. Habilitar depuración remota de Chrome.
2. Abrir YZA y seleccionar manualmente una dirección de CP 11590
   mediante una sugerencia válida de la interfaz.
3. Confirmar que la ubicación aplicada se muestra de forma persistente
   fuera del campo y de cualquier modal. No basta rellenar el formulario.
4. Ejecutar:

       & .\scripts\run_phase6_pilot.ps1 -Mode probe -Retailer farmacias-yza -UseExistingChrome

Si el sitio solo permite verificar disponibilidad de UN producto, sin
mostrar una ubicación global seleccionada, seguir en NEEDS_REVIEW y
revisar la semántica comercial de precios antes de promover YZA.
No relajar a context_verified=True ni ejecutar capture productivo.


## 27/oct 08:40 — Modal postal confirmado en imagen y prueba real

El usuario aportó captura del modal de YZA: título **Ingresa un código
postal**, texto "Introduce el código postal en donde deseas recibir tu pedido",
input con placeholder **Código Postal**, botón **CANCELAR** y botón verde
**ACEPTAR**. Además, el probe Chrome automático sigue reportando 18 tarjetas
por página y 20/7 páginas, sin CP 11590 certificado. El diagnóstico encontró
los controles "INGRESA TU DIRECCIÓN" e "Ingresa un código postal" en el DOM.

Se corrigió el extractor en scraper/retailers/yza.py:
- Detectar el input **visible** de placeholder Código Postal; si no aparece,
  abrir el selector mediante el encabezado **INGRESA TU DIRECCIÓN**.
- Completar exactamente 11590 y hacer clic visible en **ACEPTAR**.
- Esperar hasta 8 segundos a que YZA muestre el CP **aplicado** en el control
  persistente del encabezado, excluyendo modales, formularios y elementos ocultos.
- El probe vuelve a cargar la categoría y repite la verificación antes de
  permitir READY_FOR_FULL_CAPTURE; el scraper completo la repite en cada página.
- Una cifra visible en el campo editable NO certifica CP ni permite
  context_verified=True. Sin prueba persistente la respuesta es NEEDS_REVIEW.
- No usar sugerencias de autocompletado: corresponden a otra interfaz distinta.

Probar:

    git pull --ff-only
    python -m pytest tests/test_phase6_pilot.py tests/test_yza_pilot.py tests/test_farmatodo_pilot.py -v
    & .\scripts\run_phase6_pilot.ps1 -Mode probe -Retailer farmacias-yza

Si sigue CP confirmado False después de hacer clic en ACEPTAR, revisar
la captura local diagnostics/yza/postal_ui_* y la variable postal_ui
en el JSON; no iniciar captura completa sin evidencia. Para el modo Chrome
manual, usar -UseExistingChrome con un CP aplicado previamente.


## 27/oct — diagnóstico de YZA tras probar el modal Código Postal

Nuevo probe real del operador:
- Respiratorio: 18 tarjetas, 20 páginas, CP 11590 no confirmado.
- Higiene bucal: 18 tarjetas, 7 páginas, CP 11590 no confirmado.
- El inventario DOM del navegador listó múltiples controles, incluidos
  "INGRESA TU DIRECCIÓN", "Ingresa un código postal" y
  "Actualizar una dirección".

El scraper podría estar abriendo el selector general de direcciones sin
abrir el submodal postal, o pulsando ACEPTAR sin que la interfaz aplique el CP.
Para distinguir esas situaciones, se añadió un segundo paso al operador de UI:
abrir el submodal "Ingresa un código postal" si después de abrir el selector
principal todavía no se encuentra el input Código Postal visible.

El probe registra un bloque postal_flow en el JSON con requested_cp, stage,
postal_field_visible, accept_visible, field_filled, accept_clicked,
delivery_header_clicked, postal_subdialog_clicked, y, cuando proceda,
postal_field_still_visible_after_accept. Solo contiene booleanos y etiquetas
de estado: nunca registra el valor escrito en campos de direcciones.
La pantalla del diagnóstico todavía se guarda localmente en
diagnostics/yza/postal_ui_<category>_<timestamp>.png.

Para el próximo diagnóstico aislado:

    git pull --ff-only
    python -m pytest tests/test_phase6_pilot.py tests/test_yza_pilot.py tests/test_farmatodo_pilot.py -v
    & .\scripts\run_phase6_pilot.ps1 -Mode probe -Retailer farmacias-yza

**Si el reporte muestra accept_clicked=true pero context_verified=false,
no ampliar ni relajar la prueba de CP**: examinar el encabezado real después
de aceptar. Una dirección escrita o guardada sin confirmación visual del
CP 11590 no certifica el precio contextual.


## Confirmación adicional del operador: modal postal primero

Al cargar por primera vez YZA, el usuario ve inmediatamente el popup
"Ingresa un código postal" (campo "Código Postal", botón "ACEPTAR").
Por tanto, el orden correcto es:

1. Comprobar si ese popup YA ESTÁ VISIBLE y utilizarlo sin hacer clic
   en el encabezado.
2. Esperar brevemente a que se hidraten campo y botón si el título del
   popup aparece antes de que terminen de cargar sus controles.
3. Rellenar CP 11590, pulsar ACEPTAR y buscar confirmación en el
   selector de entrega visible fuera del modal.
4. Solo si NO hay popup visible, intentar abrirlo desde el encabezado.
5. Si el popup está visible y no se reconoce el formulario, mantener
   NEEDS_REVIEW y registrar stage=initial_popup_visible_but_controls_missing,
   sin intentar hacer clic detrás del modal.
6. Recargar y volver a verificar el CP antes de certificar READY_FOR_FULL_CAPTURE.

Nuevos tests offline comprueban que el modal inicial tenga prioridad
sobre el encabezado y que un popup visible sin campo no pueda pasar.
