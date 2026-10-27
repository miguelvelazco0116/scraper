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
