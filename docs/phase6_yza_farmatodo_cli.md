# Fase 6 — Farmacias YZA y Farmatodo (piloto aislado)

## Alcance vigente desde 27/oct/2026

| Retailer | Categorías | Ubicación | Navegador |
|---|---|---|---|
| Farmacias YZA | medicamentos-respiratorio, higiene-bucal | **NO se verifica CP ni sucursal** | Chrome abierto por el operador y conectado mediante CDP, como HEB |
| Farmatodo | respiratorios, cuidado-bucal, higiene-personal | **NO se verifica CP ni sucursal** | Chrome automático visible de Playwright |

**YZA ya NO requiere ni registra CP 11590.** Si su sitio muestra un
popup de ubicación, el usuario puede cerrarlo manualmente sin seleccionar
ningún CP. El scraper no intenta escribir, aceptar ni verificar un CP.

Las capturas de ambos sitios tienen alcance
ONLINE_CATALOGUE_NO_LOCATION_VERIFICATION:
- postal_code = null, store = null, store_id = null
- store_context_verified = false
- disponibilidad local y existencia en tienda **no certificadas**
- city = Catálogo online, state = Nacional: etiquetas genéricas del
  canal, **no** afirmaciones de que el precio sea uniforme en México
- los precios son los observados en la sesión del navegador; pueden variar
  según la ubicación, sin que el piloto detecte ni certifique esa variación.

Se exige integridad de producto: SKU, URL, precio positivo, ausencia de
duplicados, fecha válida y cobertura de todas las páginas de las dos
categorías de YZA (y tres de Farmatodo) antes de declarar COMPLETE_PILOT.

## Preparación del Chrome manual de YZA (igual que HEB)

1. Abrir Google Chrome personalmente.
2. Abrir chrome://inspect/#remote-debugging
3. Activar "Allow remote debugging for this browser instance" y permitir
   el acceso si Chrome presenta un cuadro de confirmación.
4. Abrir https://www.yza.mx/ en esa misma instancia y dejar la pestaña.
   **NO es necesario seleccionar localidad ni código postal.**
5. Si el popup postal tapa los productos, cerrarlo manualmente mediante
   Cancelar o la X, sin rellenar ningún CP.
6. Mantener Chrome abierto mientras dura el piloto.

El scraper encuentra el WebSocket local en DevToolsActivePort y reutiliza
la pestaña ya abierta. No lanza ni cierra Chrome en el modo CDP.
**Un solo entorno virtual `(scraper)` para TODOS los retailers.**
El `requirements.txt` principal fija Playwright 1.60.0, compatible con
el método de Chrome CDP de YZA. Se eliminó la dependencia extra exclusiva
de Fase 6. La versión unificada requiere correr tests de regresión de
los scrapers existentes antes de considerarla validada para producción.

### Actualizar y validar solo Fase 6

    cd C:\Proyectos\scraper
    git switch feat/phase6-yza-farmatodo-cli-20261026
    git pull --ff-only
    python -m pip install -r requirements.txt
    python -m pip check
    python -m pytest tests/test_phase6_pilot.py tests/test_yza_pilot.py tests/test_farmatodo_pilot.py -v

### Probe de YZA desde la misma ventana de Chrome

    & .\scripts\run_phase6_pilot.ps1 -Mode probe -Retailer farmacias-yza

Las dos categorías deben mostrar enlaces, tarjetas y paginación para
READY_FOR_FULL_CAPTURE. El resultado NO certifica una captura completa.
Si hay bloqueo, cero tarjetas o falta paginación, queda NEEDS_REVIEW.

### Probe de Farmatodo por separado

    & .\scripts\run_phase6_pilot.ps1 -Mode probe -Retailer farmatodo

El diagnóstico de Farmatodo sigue independiente. Si no identifica SKU,
queda NEEDS_SKU_REVIEW y se inspeccionan las imágenes/productos antes
de cualquier captura completa.

### Capturas piloto (solo tras probes limpios)

    & .\scripts\run_phase6_pilot.ps1 -Mode capture -Retailer farmacias-yza
    & .\scripts\run_phase6_pilot.ps1 -Mode capture -Retailer farmatodo

Archivos:
- output/yza_online_test.xlsx
- output/pilots/farmatodo/catalogo_online.xlsx
- diagnostics/yza/last_run.json
- diagnostics/farmatodo/last_run.json
- diagnostics/phase6/last_run.json

El libro anterior output/yza_cp11590_test.xlsx se considera **LEGACY**:
no se sobreescribe, no se reutiliza ni se usa para validar el nuevo
alcance online sin localidad. La configuración antigua yza-cp-11590 de
ubicaciones es histórica y no se utiliza en este piloto.

## Actualización Farmatodo 27/oct — SKU y Mostrar más

El primer probe desde el Chrome automático llegó a las tres categorías,
pero con solo seis tarjetas iniciales y ningún SKU de tarjeta:
**Respiratorios 6/460**, **Cuidado bucal 6/169** e
**Higiene personal 6/140**. Esto no fue un bloqueo del sitio.

Los productos muestran su SKU en la ficha individual como
`Sku: 7509546653518` (Colgate Total Prevención Activa Antisarro C/150 mL).
Se cambió el piloto para visitar PDP públicas y extraer el identificador
visible. Las imágenes de producto solo aportan candidatos, no certifican SKU.

La nueva primera prueba de Farmatodo:
1. Abre cada categoría en Chrome automático.
2. Pulsa **Mostrar más** al menos una vez y espera nuevas URL de producto.
3. Abre 3 páginas individuales para buscar el texto `Sku:`.
4. Reporta `probe_sku_checked`, `probe_sku_verified` y
   `probe_load_more_verified`.
5. Si los 3 SKU y el avance están probados devuelve `PROBE`;
   en caso contrario `NEEDS_SKU_REVIEW` o `NEEDS_PAGINATION_REVIEW`.
6. No abre Excel ni modifica el master durante el probe.

En captura completa, Farmatodo continúa pulsando Mostrar más hasta
alcanzar cobertura, comprueba SKU en cada PDP y genera un libro aislado
con hoja `SkuAudit` para cada producto. La validación exige que la hoja
coincida con `Concentrado` y con los conteos del resumen.

```powershell
git pull --ff-only
python -m pytest tests/test_phase6_pilot.py tests/test_yza_pilot.py tests/test_farmatodo_pilot.py -v
& .\scripts\run_phase6_pilot.ps1 -Mode probe -Retailer farmatodo
```

No volver a capturar YZA, ya reportó `COMPLETE_PILOT` con 474 productos.

## Corrección del entorno único y Browser.new_context (27/oct/2026)

El operador pidió mantener todos los scrapers en **un solo entorno virtual**
`C:\\Proyectos\\venvs\\scraper`. Por ello se unificó Playwright 1.60.0
en `requirements.txt` de la rama Fase 6 y se eliminó el archivo
`requirements-phase6-yza-cdp.txt`.

El diagnóstico real de Farmatodo falló en las tres categorías con
`Error: Please use browser.new_context()` **antes de leer productos**.
La implementación anterior llamaba a `browser.new_page(...)` directamente
sobre Chrome. Ahora `scripts/test_farmatodo.py` crea un contexto separado
mediante `browser.new_context(viewport=..., locale="es-MX")`, abre una
pestaña con `context.new_page()` y cierra **solo ese contexto y sus
pestañas** al terminar el intento. Si Farmatodo usa CDP, reutiliza el
contexto del navegador del operador sin cerrarlo. YZA manual no cambia.

El upgrade del `requirements.txt` es un cambio de dependencia común,
por lo que se deben comprobar pruebas Fases 1-5, Fase 6 y `python -m pip
check`. **No se ha validado todavía el comportamiento de las fases
productivas con Playwright 1.60 en Windows.**

```powershell
cd C:\Proyectos\scraper
git pull --ff-only
python -m pip install -r requirements.txt
python -m pip check
python -m pytest -q
& .\scripts\run_phase6_pilot.ps1 -Mode probe -Retailer farmatodo
```

El master, integración de producción y Streamlit no se alteran por
este piloto; cualquier integración futura deberá revisarse por separado.

## Criterios para integrar Fase 6 a la operación completa

**Decisión de producto:** estos pilotos NO son el entregable final. El objetivo
es un **solo proyecto integrado de scraping**, con un entorno virtual,
orquestación, control de calidad, consolidación, master, auditoría,
Streamlit y documentación unificados. La integración se desarrollará
después de certificar las capturas, no a costa de cambiar datos productivos
incompletos o ignorar restricciones.

### Puertas de entrada antes de integrar

1. **YZA:** conservar la captura piloto ya observada: 474 productos en
   dos categorías (351 + 123), con verificación de paginación, SKU, URL,
   precios y promociones, sin atribuir CP ni sucursal. Volver a comprobar
   la integridad para la fecha de integración; no declarar reutilizable
   un piloto de otra fecha.
2. **Farmatodo:** las tres categorías deben superar el probe y luego la
   captura integral. Comprobar cobertura verificable de cada catálogo,
   fin real de Mostrar más, precio y URL válidos, SKU trazables al
   texto visible de la ficha PDP, ausencia de duplicados y reporte
   SkuAudit coincidente. NO aceptar solo tres PDP de prueba como evidencia
   del total.
3. **Una instalación:** todas las fases comparten el venv `(scraper)`
   y `requirements.txt`, incluidos los flujos manuales CDP donde
   se requieren. Verificar el conjunto completo de tests y regresión
   real sobre los navegadores de los retailers existentes.
4. **Contexto honesto:** las filas YZA/Farmatodo tienen `postal_code`,
   `store` y `store_id` nulos; `store_context_verified=False`.
   Esto describe el precio observado en catálogo online, NO un precio
   único nacional ni inventario confirmado por código postal.

### Trabajo de integración posterior

1. **Contratos y datos:** mapear ambos pilotos a `main.COLUMNS`
   preservando los campos de precio actual/anterior, promoción,
   identificación y estado de disponibilidad desconocido, sin
   sobreescribir ni elevar atributos geográficos no verificados.
   Conservar la evidencia SKU/PDP en auditoría independiente.
2. **Integrador y master:** extender el modelo de fases de
   `scripts/integrate_validated_phase.py` y sus validadores para
   contemplar la **Fase 6**, sin modificar las reglas certificadas de
   Fases 1–5. Agregar previa/dry-run, conciliación de categorías y
   conteos, fecha idéntica al master activo, respaldo de XLSX y del
   puntero, escritura atómica, reporte de resultado y reversión segura.
   Una categoría parcial no debe hacer pasar la fase por completa.
3. **Ejecución de punta a punta:** incorporar YZA y Farmatodo al
   orquestador y a los wrappers de PowerShell manteniendo reintentos,
   reanudación por categoría verificada, controles de Chrome manual
   y el bloqueo de ejecuciones concurrentes. Separar con claridad
   captura de publicación al master.
4. **Dashboard Streamlit:** añadir Fase 6 y los dos retailers a
   Nueva ejecución, Resumen, alertas y trazabilidad. Mostrar claramente
   contexto online sin localidad, fecha y estado de integración; el
   dashboard NO debe presentar PROBE/COMPLETE_PILOT como publicado.
5. **Calidad de integración:** pruebas unitarias, tests de regresión de
   Fases 1–5, integración con workbooks de ejemplo, escenarios de falla,
   recuperación, control de duplicados y auditoría del master antes y
   después de integrar. Mantener copia recuperable de los archivos
   previos, con validación de hashes y puntero.
6. **Liberación:** realizar una prueba completa de captura +
   validación + previa + integración sobre la fecha actual;
   verificar el master, los reportes y el dashboard. Solo entonces
   marcar la fase completa y fusionar el PR o PR de integración
   correspondiente. No considerar integración terminada por el
   mero resultado `COMPLETE_PILOT`.

**Estado actual:** YZA terminó su piloto, Farmatodo continúa en
diagnóstico. No se ha iniciado el trabajo de integración productiva
y el master activo debe permanecer inalterado hasta cumplir todas
las puertas anteriores.

## Restricciones

Fase 6 sigue en PR borrador. No se edita el master de las Fases 1–5,
la integración principal ni el dashboard Streamlit. Completar un piloto
no integra los resultados en producción.

El WebSocket de depuración concede acceso de control al Chrome del usuario:
no compartirlo y desactivar la depuración cuando haya terminado el trabajo.

## 27/oct — Corrección de paginación y precios YZA

El operador adjuntó capturas de:
- Paginador UI: **PÁGINAS: 1 2 3 4 ... 7**, representado por
  botones reales (no enlaces de la forma `?p=2`).
- Tarjeta Colgate PerioGard 250 ml: **$213.60 Oferta**, **$267.00 Antes**;
  etiqueta visual "Compra 3 y llévate +1 gratis", válida en sucursal.
- Tarjeta Bexident Aftas Gel 8 ml: **$221.85 Oferta**,
  **$246.50 Antes**. Su botón "Ver disponibilidad" NO permite inferir
  disponibilidad local sin una verificación individual.

La captura anterior falló 3/3 en Respiratorio y 3/3 en Higiene bucal
porque el navegador mostraba los mismos productos al usar URLs
`?p=2`. Ahora la captura:
1. Abre la URL de categoría una sola vez.
2. Guarda las URLs de producto visibles en la página inicial.
3. Pulsa el **botón numerado** de la página 2 y espera a que aparezcan
   enlaces a productos NUEVOS. Nunca pulsa la elipsis para saltarse
   páginas.
4. Repite de manera secuencial hasta la última página identificada,
   verificando que hay nuevos SKU y precios válidos; cada página muestra
   un resumen en consola.
5. Falla con PARTIAL si el botón no aparece, se repite el catálogo o no
   termina la carga; conserva 3 intentos por categoría.
6. El probe también hace clic en página 2 y exige productos distintos
   antes de devolver READY_FOR_FULL_CAPTURE. Por tanto, el siguiente
   probe valida la navegación real, no solo el número de páginas.
7. Se registra `promotion=Oferta` cuando el precio actual es menor que
   el anterior o la tarjeta muestra explícitamente Oferta. Si el
   HTML contiene un nombre de imagen de promoción reconocible, también
   se conserva la etiqueta, aclarando cuando es válida solo en sucursal.
   No se certifican condiciones generales, disponibilidad o aplicación
   del beneficio en e-commerce solo por ver una etiqueta visual.

Los precios reales pueden cambiar; los valores de las capturas son
ejemplos para probar el parser, no constantes de producción.

### Comandos para volver a validar

    git pull --ff-only
    python -m pytest tests/test_phase6_pilot.py tests/test_yza_pilot.py tests/test_farmatodo_pilot.py -v
    & .\scripts\run_phase6_pilot.ps1 -Mode probe -Retailer farmacias-yza

Solo tras ver `Pagina 2 verificada: True` en ambas categorías, ejecutar:

    & .\scripts\run_phase6_pilot.ps1 -Mode capture -Retailer farmacias-yza

El master productivo sigue intacto.

## Historial mínimo de decisiones

- Primeros diagnósticos: YZA presentó 18 tarjetas por página y paginación
  de 20 y 7 páginas. Se trabajó inicialmente con CP fijo 11590.
- Se adoptó Chrome manual por CDP, igual que HEB, con descubrimiento del
  endpoint desde DevToolsActivePort. YZA confirmó ambos probes con CP.
- **Decisión más reciente:** eliminar toda validación y atribución de
  localidad de YZA. El CP deja de ser necesario; se conserva el browser CDP.
- Farmatodo tuvo primeras pruebas con 9/460, 6/169 y 6/140 tarjetas
  visibles, pero SKU no identificado. No confundir esos probes con captura.
