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
