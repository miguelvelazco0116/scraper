# Farmacias YZA · Piloto con CP 11590

## Estado

**Piloto de captura, no retailer productivo integrado.** No modifica el master,
Control, `EXPECTED` ni el cierre de las fases 1–5.

Categorías:
- `medicamentos-respiratorio` → https://www.yza.mx/medicamentos/respiratorio/
- `higiene-bucal` → https://www.yza.mx/cuidado-personal/higiene-bucal/

Configuración postal fija: `11590` (Miguel Hidalgo, CDMX).
No se inventa sucursal, ni disponibilidad, ni stock local.

## Uso desde Streamlit

1. Activar `C:\Proyectos\venvs\scraper\Scripts\Activate.ps1` en PowerShell.
2. Iniciar `python -m streamlit run streamlit_app.py --server.port 8501`.
3. Abrir Google Chrome con remote debugging habilitado en
   `chrome://inspect/#remote-debugging`.
4. Abrir una pestaña de `https://www.yza.mx/`; seleccionar manualmente,
   desde el selector de ubicación, una dirección sugerida con CP **11590**.
   Si el sitio no acepta ese CP o no lo muestra como ubicación aplicada,
   el piloto debe fallar, no simular confirmación.
5. En Streamlit → **Nueva ejecución** → **Fase 4** →
   **Farmacias YZA** → **Solo captura**, confirmar y ejecutar.
6. Revisar el log y `output/yza_cp11590_test.xlsx`
   (Concentrado y Resumen); `diagnostics/yza/last_run.json`.
7. Los casos reintentan la **misma categoría tres veces** antes de avanzar.
   Las categorías válidas del mismo día se reutilizan.

## Requisitos de validación

- CP 11590 visible y aplicado en un componente persistente de ubicación
  del sitio, fuera del campo editable/autocompletado.
- Un URL YZA auténtico con identificador `MXYZ_...`.
- Precios positivos y orden de promociones razonable.
- Listado paginado completamente; si el paginador no se reconoce,
  conservar el estado como parcial y revisar su DOM.
- Sin duplicados, sin registros de otros días y sin atribuir
  disponibilidad a una dirección solo porque el botón de entrega aparezca.

## Antes de promover a producción

Verificar en Chrome real: interacción del CP, cobertura de entrega para 11590,
selectors DOM de producto, paginación y tarifa efectiva por ubicación.
Solo entonces incluir YZA en `EXPECTED[4]`, runner integral de Fase 4,
fuentes de integración, auditoría y resultados esperados (14 retailers, 68 casos).
Esta migración debe ejecutarse con respaldo, porque altera el contrato de
completitud de Fase 4 y las dependencias de la Fase 5.
