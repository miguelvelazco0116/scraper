# Soriana · Gastrointestinales y disponibilidad online

## Alcance

**Primer paso: piloto de línea de comandos**. Se agregó a
`config/soriana/categories.yaml` como `enabled: false` para que las seis
categorías productivas de la Fase 2 no cambien hasta aprobar la prueba.

URL solicitada (con su ruta original, incluso el nombre `gastrointenstinales`):

https://www.soriana.com/farmacia/gastrointenstinales/?cgid=gastrointestinales

## Regla de disponibilidad (por tarjeta)

- `Agregar +` o `Agregar` → `availability_status=AVAILABLE`,
  `is_available=True` (`1`).
- `Agotado en línea` → `availability_status=UNAVAILABLE`,
  `is_available=False` (`0`).
- Botón no reconocido o evidencia contradictoria →
  `availability_status=UNKNOWN`, `is_available=None`.

`availability_raw` retiene la evidencia textual, y **la existencia de un
precio NO implica inventario**. Nunca descartar productos agotados. Esto
representa **disponibilidad online en la sesión de Soriana**; no certifica
existencias físicas ni tiendas seleccionadas fuera de esa sesión.

## Ejecución piloto

Solo si no hay ejecución productiva activa y `git status --short` está limpio:

```powershell
cd C:\Proyectos\scraper
& C:\Proyectos\venvs\scraper\Scripts\Activate.ps1
git fetch origin
git switch --track origin/feat/soriana-gastro-availability-cli-20261026
python -m pytest tests/test_soriana_gastro_availability.py -v
```

Si la rama ya existe localmente, usar `git switch
feat/soriana-gastro-availability-cli-20261026` y `git pull --ff-only`.

Abrir **Google Chrome existente**, habilitar remote debugging en
`chrome://inspect/#remote-debugging` y dejar abierta una pestaña de Soriana.

```powershell
& .\scripts\test_soriana_gastro.ps1
```

El wrapper detecta Python del entorno virtual y la conexión CDP. El scraper
recorre la paginación con la sesión existente; si una ejecución es parcial,
reintenta hasta **3 veces la misma categoría**. No evita CAPTCHAs ni bloqueos.

## Artefactos

- `output/soriana_gastrointestinales_pilot.xlsx`
  - `Concentrado`: cada SKU, precio actual/regular, fecha, categoría,
    `availability_status`, `is_available` y `availability_raw`.
  - `Resumen`: productos, objetivo, cobertura, páginas, disponibles,
    agotados y `UNKNOWN`.
- `diagnostics/soriana_gastrointestinales/last_run.json` y
  HTML/capturas de diagnóstico.
- El piloto **no altera** `output/soriana_manual_phase2_test.xlsx`, el
  concentrado productivo ni el puntero `current_master.json`.

Requisitos piloto `COMPLETE`: al menos 90% de cobertura del total visible,
recorrido del paginador completo, 90% de SKU, precio y URL, además de al menos
90% de disponibilidad explícitamente clasificada en tarjetas. Ninguna señal
ausente se convierte silenciosamente en 0.

## Promoción a producción (segundo cambio posterior al piloto)

Tras verificar el Excel y pruebas reales con Chrome:

1. Marcar `gastrointestinales` como `enabled: true`.
2. Cambiar Soriana de 6 a 7 casos esperados en el contrato productivo
   (`scripts/integrate_complete_retailers.py`), y actualizar los
   tests, comprobaciones del auditor y los mensajes de fase.
3. Correr Fase 2 nuevamente y validar integraciones con respaldos.
4. Nuevo total esperado: **13 retailers, 67 casos**; Fase 2:
   Farmacias Guadalajara 4 + Soriana 7 = 11.
5. Comprobar el efecto sobre el puntero del master y la ejecución cronológica
   antes de hacer el merge.
