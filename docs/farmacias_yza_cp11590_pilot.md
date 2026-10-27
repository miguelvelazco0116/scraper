# Farmacias YZA · Validación inicial por línea de comandos

**Estado: piloto CLI únicamente.** Aún no se agrega al panel Streamlit ni
al master productivo; las cinco fases y sus 13 retailers permanecen intactos.

**CAMBIO VIGENTE — 27/oct/2026:** YZA opera como Farmacias San Pablo:
el operador abre Chrome con depuración local y selecciona CP **11590**
manualmente; el scraper se conecta a esa pestaña mediante CDP, **no**
modifica el código postal, comprueba su aplicación visible antes de
navegar y en cada página. El master productivo permanece intacto.

Instrucciones actualizadas: docs/phase6_yza_farmatodo_cli.md,
apartado "Modo San Pablo". Las instrucciones antiguas de Chrome
automático que aparecen más abajo son antecedentes del piloto,
no deben usarse para la próxima validación.

## Alcance
- Medicamentos → Respiratorio: https://www.yza.mx/medicamentos/respiratorio/
- Cuidado personal → Higiene bucal: https://www.yza.mx/cuidado-personal/higiene-bucal/
- CP objetivo: **11590**. Una captura solo puede declararse válida cuando YZA
  muestra explícitamente una dirección seleccionada para dicho código en una
  zona persistente de la página (no basta escribir "11590" en un campo).
- Chrome abierto y preparado **por el operador**, CDP local obligatorio en Fase 6. Tres intentos **por categoría**.
- No se certifica disponibilidad por tienda si YZA no proporciona pruebas.

## Pasos en Windows

No cambiar de rama, actualizar el código o reiniciar Streamlit mientras haya
un scraper/productor en marcha. Con `git status --short` limpio:

```powershell
cd C:\Proyectos\scraper
& C:\Proyectos\venvs\scraper\Scripts\Activate.ps1
git fetch origin
git switch --track origin/feat/yza-cp11590-pilot-20261026
python -m pytest tests/test_yza_pilot.py -q
```

Si la rama ya existe localmente, ejecutar `git switch feat/yza-cp11590-pilot-20261026`
y `git pull --ff-only` en lugar de `--track`.

En la rama de Fase 6 `feat/phase6-yza-farmatodo-cli-20261026`,
Chrome se inicia automáticamente desde el scraper para el primer intento,
sin abrir previamente una pestaña ni activar CDP.
Si YZA muestra un bloqueo o no confirma una dirección CP 11590, abrir
manualmente una pestaña de YZA en Chrome, activar
`chrome://inspect/#remote-debugging`, seleccionar domicilio CP 11590
y conectar por `-CdpUrl` (o `-UseExistingChrome` en el runner de Fase 6).
El código exige una evidencia persistente del CP: completar el formulario
sin confirmarlo no autoriza una captura.

Primero diagnóstico **sin captura completa**:

```powershell
& .\scripts\test_yza.ps1 -Probe -Category higiene-bucal
& .\scripts\test_yza.ps1 -Probe -Category medicamentos-respiratorio
```

Leer `diagnostics/yza/probe_*.json`. Si `context_verified=False`
o `pager_last_page` es `null`, revisar la selección postal o la paginación
antes de lanzar las capturas completas; nunca dar por verificado el CP.

Con diagnóstico válido:

```powershell
& .\scripts\test_yza.ps1 -Category higiene-bucal
& .\scripts\test_yza.ps1 -Category medicamentos-respiratorio
```

La captura piloto produce `output/yza_cp11590_test.xlsx` (hojas
`Concentrado`, `Resumen`) y `diagnostics/yza/last_run.json`. Reutiliza
las categorías que ya pasaron controles el mismo día. Las incompletas
intentan hasta tres veces y después pasan a la otra categoría.

## Condiciones para añadir al panel más tarde
- Dos categorías, CP 11590 confirmado, paginación y conteos validados.
- SKU, URL, precios positivos, sin duplicados, y contexto postal verificable.
- Pruebas sin errores y corrida real de Chrome validada.
- Después se desarrollará la integración a Streamlit. La migración al master
  implicará actualizar contrato `EXPECTED`, integrador y auditor y crear un
  respaldo, para no romper la ejecución cronológica.
