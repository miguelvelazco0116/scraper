# Fase 6 — Farmacias YZA exclusivamente (piloto CLI aislado)

**Alcance vigente desde el 27 de octubre de 2026.**
Por decisión del operador, Farmatodo se retiró del alcance de Fase 6.
El código descartado sigue recuperable en el historial de Git, pero no
se ejecuta, valida ni integra. Mercado Libre se desarrollará en Fase 7
como proyecto de extracción sin interfaz gráfica nueva.

## Contrato YZA

- Retailer: **Farmacias YZA**.
- Categorías: `medicamentos-respiratorio`, `higiene-bucal`.
- Piloto previo reportado: **474 productos** (351 + 123); esta cifra es
  histórica, no equivale a una validación vigente del master.
- Contexto: catálogo online público, **sin exigir/verificar CP o sucursal**.
  `postal_code=null`, `store=null`, `store_id=null`,
  `store_context_verified=false`.
- No presentar precios como válidos para todo México ni interpretar
  disponibilidad de tienda sin comprobarla.
- Navegador: Chrome abierto por el operador y conectado por CDP, como HEB.
- Entorno: **único venv `(scraper)`**, con `requirements.txt` compartido
  y Playwright 1.60.0 (sin requirements separados).
- Resultados: `output/yza_online_test.xlsx`,
  `diagnostics/yza/last_run.json` y `diagnostics/phase6/last_run.json`.
- Estado `COMPLETE_PILOT` solo si las **dos** categorías del día actual
  superan los controles de SKU, URL, precio, duplicados, paginación y
  contexto online. Esto NO publica el master productivo.

## Preparar Chrome (Windows)

1. Abrir Google Chrome y una pestaña `https://www.yza.mx/`.
2. Abrir `chrome://inspect/#remote-debugging` en la misma instancia.
3. Activar `Allow remote debugging for this browser instance`.
4. Aprobar la conexión local si Chrome la solicita. No hace falta
   introducir código postal; si un popup lo tapa, cerrarlo manualmente.
5. Mantener Chrome abierto mientras se ejecuta el piloto.

El wrapper detecta `DevToolsActivePort` y la dirección WebSocket
del propio Chrome, sin exigir que el puerto sea 9222.

## Validar y ejecutar

```powershell
cd C:\Proyectos\scraper
git switch feat/phase6-yza-farmatodo-cli-20261026
git pull --ff-only

python -m pip check
python -m pytest -q

# Diagnóstico de las dos categorías, sin Excel productivo
& .\scripts\run_phase6_pilot.ps1 -Mode probe -Retailer farmacias-yza

# Captura de YZA únicamente
& .\scripts\run_phase6_pilot.ps1 -Mode capture -Retailer farmacias-yza
```

`-Retailer all` equivale ahora únicamente a YZA. La opción Farmatodo
ya no existe; pedirla debe fallar explícitamente. El probe no certifica
captura integral, y los resultados incompletos deben quedar separados
del master y fuera de cualquier integración.

## Integración final pendiente

Después de verificar una captura YZA del mismo día:
1. Auditar XLSX/Resumen, categorías, SKU/URL/precios y alcance online.
2. Preparar extensión de la Fase 6 en el integrador productivo, con
   previsualización, respaldo y rollback, **sin alterar reglas Fases 1–5**.
3. Añadir YZA al runner y al Streamlit existentes, sin una segunda GUI.
4. Ejecutar suite de regresión y comprobación de master antes/después.
5. Solo publicar YZA al master cuando esos controles pasen.

**Mercado Libre:** siguiente retailer de Fase 7, desarrollo separado,
solo scraping, pensado para el esquema común del proyecto.

## Integración segura al master (nuevo)

Con las cinco fases anteriores completas **en el master fechado del día**,
y tras una captura `COMPLETE_PILOT` de YZA de ese mismo día:

```powershell
# Diagnóstico / captura (Chrome CDP del operador preparado antes)
& .\scripts\run_phase6_pilot.ps1 -Mode probe -Retailer farmacias-yza
& .\scripts\run_phase6_pilot.ps1 -Mode capture -Retailer farmacias-yza

# Valida sin modificar master ni puntero
python .\scripts\integrate_phase6_yza.py --preview

# Sólo después de revisar preview: integración con respaldo y rollback
python .\scripts\integrate_phase6_yza.py --commit

# Audita inmediatamente sin volver a capturar
python .\scripts\audit_master_phases.py
```

El integrador independiente requiere fecha y categorías exactas, precio,
SKU, URL de YZA y ausencia de atribución de CP/tienda; exige que Fases 1–5
figuren completas en el puntero activo del día. Sólo sustituye la partición
YZA en `Concentrado` y `Resumen`, añade `FASE 6` al `Control` y
actualiza `current_master.json`. Antes de escribir respalda ambos archivos
bajo `output/checkpoints_validos/before_phase6_yza_*.{xlsx,json}`.

No se importa el histórico del piloto ni se ejecuta integración automática.
La validación live en Windows es condición indispensable para tener YZA
incorporada al master; las pruebas de GitHub no acceden al Chrome del operador.


## Streamlit: integración de Farmacias YZA (Fase 6)

En Nueva ejecución → Fase 6 → Todas las tiendas se ofrecen **Solo captura**,
**Captura + integración** e **Integrar capturas existentes**. La opción
individual **Farmacias YZA** solo ejecuta la captura de dos categorías.

- La captura requiere Chrome abierto por el operador y depuración CDP.
- Integrar capturas existentes NO vuelve a ejecutar Chrome ni scraper.
- El integrador exige master del día y Fases 1–5 COMPLETE.
- Si YZA ya está certificada responde `YA_EXISTE_EN_MASTER`, sin modificar.
- Si falta Excel responde `SIN_CAPTURA`, sin modificar.
- Si el Excel existe pero falla la validación responde `CAPTURA_NO_VALIDADA`.
- Si la captura cumple los gates responde `LISTO_PARA_INTEGRAR`.
- La integración preserva otras fases y aplica respaldo y rollback.

**Importante:** YZA se mantiene como catálogo online, sin certificar
CP, tienda ni disponibilidad por localidad. Farmatodo está fuera del alcance.

Para previsualizar sin escribir: `./scripts/integrate_phase6_yza.ps1 -Preview`
desde PowerShell, o selecciona la opción correspondiente en Streamlit.

Las pruebas offline no sustituyen validar una captura real de YZA en Windows.