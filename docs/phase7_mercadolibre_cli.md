# Fase 7 — Mercado Libre México (solo scraping, CLI aislada)

**Estado: MVP técnico inicial, sin certificación live ni integración al master.**
**Rama independiente:** `feat/phase7-mercadolibre-cli-20261027`
(basada en `main`, no en el PR de YZA). El objetivo final es integrar
los retailers certificados en un solo proyecto y un solo dashboard.

## Referencia técnica

Repositorio estudiado:
https://github.com/PabloAlaniz/MercadoLibre-Scraper

Solo se aprovecharon sus ideas generales: solicitudes HTTP públicas,
parseo de HTML, paginación por resultados y separación entre extracción
y exportación. No se ha copiado su interfaz Dash/Flask/Socket.IO ni su
código fuente literalmente. El README del autor menciona MIT, pero
debemos comprobar el permiso/licencia real antes de copiar código.

## Alcance del MVP

- Mercado Libre **México** y búsquedas de texto parametrizables.
- `requests` + `beautifulsoup4`: páginas públicas de resultados.
- Solo datos visibles: título, precio actual, precio anterior mostrado,
  promoción visible, ID de publicación cuando aparezca, ID de producto
  de catálogo cuando corresponda, vendedor si está visible, enlace,
  posición en el resultado y si está marcado como patrocinado.
- El **ID de publicación MLM NO es SKU/EAN**. El campo `sku` queda nulo
  hasta existir una validación independiente por producto.
- Moneda MXN, sin reglas argentinas ni conversión USD/ARS.
- Paginación usando únicamente enlaces `Siguiente` realmente
  publicados; sin inventar parámetros `_Desde_` o URLs.
- Deduplicación por identificador de publicación/catálogo y URL pública.
- Captura secuencial, espera mínima de 1 segundo entre páginas;
  no se incluye evasión de CAPTCHA, rotación de identidades ni API oculta.
- Errores HTTP, cambio de destino, paginación no verificable o diferencias
  con el total publicado se reportan como **PARTIAL** y nunca como una
  captura ficticiamente completa.
- `COMPLETE` solo si el HTML presenta fin explícito de paginación
  y el total publicado coincide con el recuento capturado.
- Búsqueda, ranking y precios son **observaciones de la sesión**:
  no representan precios regionales ni inventario de un vendedor
  para determinado código postal.

## Instalación en el único entorno virtual

La rama contiene `requests` y `beautifulsoup4` en el
`requirements.txt` principal. Retiene Playwright 1.60.0 para no
romper el entorno compartido con la Fase 6 YZA.

```powershell
cd C:\Proyectos\scraper
git fetch origin
git switch feat/phase7-mercadolibre-cli-20261027
git pull --ff-only
python -m pip install -r requirements.txt
python -m pip check
python -m pytest -q tests/test_mercadolibre_pilot.py
```

## Primera prueba de scraping (sin GUI)

```powershell
python .\scripts\test_mercadolibre.py `
  --query "pasta dental colgate" `
  --max-pages 2 `
  --max-results 50 `
  --delay-seconds 1.5
```

En una búsqueda con muchos resultados, el estado esperado de esta
prueba limitada es **PARTIAL** (límite solicitado); esto no implica
fallo del parser. Si devuelve 403, 429 o un bloque de verificación,
el scraper debe detenerse y dejar evidencia, no evadir ese bloqueo.
No se ejecutaron capturas reales en el entorno del operador todavía.

## Salidas aisladas

- Excel `output/pilots/mercadolibre/FECHA_BUSQUEDA.xlsx`:
  - `Concentrado`: esquema `main.COLUMNS`, `sku` nulo, precios MXN.
  - `Resumen`: páginas, estado, volumen y total publicado.
  - `Publicaciones`: publicación MLM, ID de catálogo (si existe),
    URL, vendedor, patrocinado, posición, promoción y moneda.
- JSON `diagnostics/mercadolibre/FECHA_BUSQUEDA.json`
  con traza por página y motivo de cualquier `PARTIAL`.
- Las salidas no se conectan a `current_master.json`, no se publican
  al master de Fases 1–6 y no modifican Streamlit.

## Pendiente antes de producción

1. Verificar funcionamiento live de las clases CSS usadas en el
   catálogo público actual (pueden cambiar de versión).
2. Revisar condiciones de acceso, robots y las APIs oficiales
   disponibles; no operar contra bloqueos o sin autorización.
3. Definir conjunto de búsquedas/categorías, filtrado de publicaciones
   patrocinadas y cómo comparar vendedores del mismo producto.
4. Auditar SKU/EAN de productos cuando sea accesible explícitamente,
   sin confundirlos con los IDs de publicación o catálogo.
5. Pruebas Windows, retorno consistente por página, capturas con fecha,
   resiliencia y criterios de cobertura según alcance realmente
   accesible.
6. Solo al certificar, integrar en runner/master/Streamlit existentes
   con preview, respaldo y rollback.

**Farmatodo está suspendido; Fase 6 solo contempla YZA.**
