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

## Preparar Chrome

Abrir Chrome con remote debugging habilitado desde
chrome://inspect/#remote-debugging, y una pestaña en www.yza.mx.
Seleccionar en la UI de YZA una dirección real con CP 11590. Si el
sitio no confirma la selección, la prueba falla; no se inventa contexto.

## Prueba A: cinco diagnósticos

    & .\scripts\run_phase6_pilot.ps1 -Mode probe -Retailer all

Dos diagnósticos YZA y tres Farmatodo. Un resultado READY_FOR_CAPTURE
es solo una señal de que pueden intentarse capturas; NO certifica los
cinco catálogos completos.

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
