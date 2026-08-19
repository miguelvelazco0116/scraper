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
| Farmacias San Pablo | Implementado como catálogo online; GitHub-hosted recibe 403 Access Denied de Akamai | Medicamentos > Gripe y tos > Descongestionantes; Salud sexual > Bienestar sexual > Preservativos; Cuidado personal y belleza > Cuidado bucal > Enjuagues bucales; Cuidado personal y belleza > Cuidado bucal > Pastas dentales |

## Ejecución recomendada

- **Soriana:** GitHub-hosted Actions.
- **Chedraui:** GitHub-hosted Actions con Chedraui Selecto México Polanco / tienda 232.
- **Farmacias del Ahorro:** GitHub-hosted Actions, contexto `fahorro-online`.
- **Farmacias Guadalajara:** contexto `fg-online`; si la red no recibe respuesta del dominio oficial se clasifica `NETWORK_UNAVAILABLE`.
- **Farmacias San Pablo:** contexto `san-pablo-online`. La implementación usa navegador y ficha individual; si Akamai devuelve 403 se clasifica `BLOCKED`. Debe ejecutarse desde una red que tenga acceso normal al sitio oficial.
- **Walmart:** GitHub-hosted Actions reutilizando una sesión Playwright verificada manualmente.
- No se automatizan CAPTCHAs ni desafíos de identidad y no se evaden controles de acceso.

## Farmacias San Pablo

Contexto:

```text
id: san-pablo-online
city: Catálogo online
state: Nacional
store: null
store_id: null
postal_code: null
```

Categorías:

```text
descongestionantes -> Medicamentos > Gripe y tos > Descongestionantes
preservativos      -> Salud sexual > Bienestar sexual > Preservativos
enjuagues-bucales  -> Cuidado personal y belleza > Cuidado bucal > Enjuagues bucales
pastas-dentales    -> Cuidado personal y belleza > Cuidado bucal > Pastas dentales
```

Identificadores conocidos del storefront:

```text
Descongestionantes: 060070004
Enjuagues bucales:  030040003
Pastas dentales:    030040007
```

Ejemplos:

```bash
python main.py --retailer farmacias-san-pablo --category descongestionantes --location san-pablo-online
python main.py --retailer farmacias-san-pablo --category preservativos --location san-pablo-online
python main.py --retailer farmacias-san-pablo --category enjuagues-bucales --location san-pablo-online
python main.py --retailer farmacias-san-pablo --category pastas-dentales --location san-pablo-online
```

La categoría se recorre con `currentPage`. Cuando existe una ficha individual `/p/`, se utiliza como fuente preferida para SKU, nombre, marca y precio. El precio vigente prioriza `h3.priceTotal`; los precios de recomendaciones no se usan como sustituto. Si una cuadrícula no expone enlaces individuales, existe un fallback conservador a tarjeta de categoría. El contexto queda con `store_context_verified=False`, porque no se solicitó una sucursal física.

Validación de conectividad del 19 de agosto de 2026: el runner GitHub-hosted de Ubuntu recibió HTTP 403 `Access Denied` en las cuatro rutas probadas, incluida la categoría conocida `030040003`. El scraper reporta este caso como `BLOCKED` y no crea filas parciales ni inventadas.

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

Validación live final del 19 de agosto de 2026 (workflow dedicado #54, run `32302275204`):

```text
Congestión nasal: 54 productos
Preservativos: 72 productos
Enjuagues bucales: 41 productos
Cremas dentales: 113 productos
Total: 280 filas de categoría
```

Cobertura de la corrida final:

```text
SKU:            280 / 280
Precio actual:  280 / 280
Precio regular: 280 / 280
URL:            280 / 280
Tests:           48 / 48
```

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

El runner incluye Soriana, Chedraui, Farmacias Guadalajara, Farmacias del Ahorro, Farmacias San Pablo y Walmart. Continúa procesando los casos, concentra las categorías exitosas y distingue `SUCCESS`, `BLOCKED`, `NETWORK_UNAVAILABLE`, `STORE_CONTEXT_ERROR`, `EMPTY` y `ERROR`.

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
