# Scraper multi-retailer

Base modular para extraer catálogos públicos de retailers de México, generar un único Excel consolidado y ejecutar validaciones desde GitHub Actions.

## Retailers

| Retailer | Estado | Categorías implementadas |
|---|---|---|
| Soriana | Implementado y validado | Cuidado bucal; Cuidado del hogar > Limpiadores; Cuidado del hogar > Limpiadores > Detergentes; Cuidado personal > Afeitado y depilación > Afeitado y depilación para dama |
| Walmart | Implementado; requiere sesión verificada portable para SC Toreo | Cuidado bucal; Cuidado de la ropa; Depilación y rasurado |
| Chedraui | Implementado para Chedraui Selecto México Polanco (232) | Higiene bucal; Lavandería |
| Farmacias Guadalajara | Código/configuración implementados; el dominio no respondió desde GitHub-hosted durante la validación | Vías respiratorias; Lavandería; Cuidado bucal; Preservativos |
| Farmacias del Ahorro | Implementado y validado como catálogo online nacional | Farmacia > Gripa y tos > Congestión nasal; Bienestar sexual > Preservativos; Cuidado personal > Higiene bucal > Enjuagues bucales; Cuidado personal > Higiene bucal > Cremas dentales |

## Ejecución recomendada

- **Soriana:** GitHub-hosted Actions.
- **Chedraui:** GitHub-hosted Actions con Chedraui Selecto México Polanco / tienda 232.
- **Farmacias del Ahorro:** GitHub-hosted Actions, contexto `fahorro-online`. El storefront usa Empathy Search; el scraper obtiene el `categoryId` vigente desde la página oficial y pagina la misma API pública del storefront hasta `catalog.pagination.total`.
- **Farmacias Guadalajara:** contexto `fg-online`; si la red no recibe respuesta del dominio oficial se clasifica `NETWORK_UNAVAILABLE`.
- **Walmart:** GitHub-hosted Actions reutilizando una sesión Playwright verificada manualmente.
- No se automatizan CAPTCHAs ni desafíos de identidad.

## Farmacias del Ahorro

Contexto:

```text
id: fahorro-online
city: Catálogo online
state: Nacional
store: null
store_id: null
postal_code: null
```

Categorías:

```text
congestion-nasal  -> Farmacia > Gripa y tos > Congestión nasal
preservativos     -> Bienestar sexual > Preservativos
enjuagues-bucales -> Cuidado personal > Higiene bucal > Enjuagues bucales
cremas-dentales   -> Cuidado personal > Higiene bucal > Cremas dentales
```

Ejemplos:

```bash
python main.py --retailer farmacias-del-ahorro --category congestion-nasal --location fahorro-online
python main.py --retailer farmacias-del-ahorro --category preservativos --location fahorro-online
python main.py --retailer farmacias-del-ahorro --category enjuagues-bucales --location fahorro-online
python main.py --retailer farmacias-del-ahorro --category cremas-dentales --location fahorro-online
```

Para descargar las cuatro categorías en una sola ejecución y un solo Excel:

```bash
python scripts/run_farmacias_del_ahorro.py --category all --location fahorro-online --fresh
```

El extractor usa los campos `sku`, `ecommTitle`, `ecommBrand`, `ecommUrlKey`, `currentPrice` y `previousPrice` que utiliza el catálogo online. Si `previousPrice` no está informado, `price_regular` se iguala al precio actual para mantener una salida consistente. El contexto se registra como `online_catalog_empathy_nacional` y no se atribuye a una sucursal física.

Validación live del 19 de agosto de 2026:

```text
Congestión nasal: 54 productos
Preservativos: 73 productos
Enjuagues bucales: 41 productos
Cremas dentales: 113 productos
Total: 281 filas de categoría
```

En esa validación hubo cobertura completa de SKU, precio actual y URL en las cuatro categorías.

## Farmacias Guadalajara

Contexto `fg-online`, sin sucursal asignada. En las pruebas del 19 de agosto de 2026, runners hospedados oficiales de GitHub resolvieron el dominio/Akamai pero no recibieron respuesta HTTP. El scraper distingue ese caso como `NETWORK_UNAVAILABLE` y no crea datos parciales o inventados.

## Chedraui / Polanco

```text
id: chedraui-polanco
store: Chedraui Selecto México Polanco
store_id: 232
postal_code: 11500
city: Miguel Hidalgo
state: CDMX
```

Ejemplos:

```bash
python main.py --retailer chedraui --category higiene-bucal --store chedraui-polanco
python main.py --retailer chedraui --category lavanderia --store chedraui-polanco
```

## Walmart / SC Toreo

```text
id: sc-toreo
store: SC Toreo
store_id: 2344
postal_code: 11220
city: Miguel Hidalgo
state: CDMX
```

Para preparar la sesión portable:

```bash
pip install -r requirements.txt
playwright install chromium
python scripts/export_walmart_session.py
```

Completa manualmente cualquier verificación y confirma SC Toreo. Guarda el contenido de `walmart_session.secret.txt` en el Repository Secret `WALMART_SESSION_GZIP_B64`. No se automatizan verificaciones de identidad.

## Ejecución completa

Con una sesión Walmart disponible:

```bash
python scripts/run_all_retailers.py --walmart-profile-dir .walmart_profile
```

El runner incluye Soriana, Chedraui, Farmacias Guadalajara, Farmacias del Ahorro y Walmart. Continúa procesando los casos, concentra las categorías exitosas y distingue `SUCCESS`, `BLOCKED`, `NETWORK_UNAVAILABLE`, `STORE_CONTEXT_ERROR`, `EMPTY` y `ERROR`.

## Output

Todas las categorías exitosas se concentran en:

```text
output/concentrado_scraper.xlsx
```

Hojas:

- `Concentrado`
- `Resumen`

## Pruebas

```bash
pytest -q
```
