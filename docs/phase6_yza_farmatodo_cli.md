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
Para el modo Chrome 144+ con chrome://inspect se usa Playwright 1.60 en
el piloto, sin modificar requirements.txt del proyecto productivo.

### Actualizar y validar solo Fase 6

    cd C:\Proyectos\scraper
    git switch feat/phase6-yza-farmatodo-cli-20261026
    git pull --ff-only
    python -m pip install -r requirements-phase6-yza-cdp.txt
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
