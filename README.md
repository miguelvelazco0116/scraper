# Scraper multi-retailer

Base modular para extraer catálogos públicos de retailers de México mediante **Python + Playwright**, generar un único Excel consolidado y ejecutar pruebas desde GitHub Actions.

## Retailers

| Retailer | Estado | Categorías implementadas |
|---|---|---|
| Soriana | Implementado y validado | Cuidado bucal; Cuidado del hogar > Limpiadores; Cuidado del hogar > Limpiadores > Detergentes; Cuidado personal > Afeitado y depilación > Afeitado y depilación para dama |
| Walmart | Implementado; requiere una sesión verificada portable para SC Toreo | Belleza y cuidado personal > Higiene y cuidado personal > Cuidado bucal; Limpieza del hogar y cuidado personal > Cuidado de la ropa; Belleza y cuidado personal > Depilación y rasurado |
| Chedraui | Implementado para Chedraui Selecto México Polanco (232) | Cuidado e higiene personal > Higiene bucal; Supermercado > Limpieza del hogar > Lavandería |
| Farmacias Guadalajara | Código y configuración implementados; requiere una red que pueda recibir respuesta del dominio oficial | Medicina > Respiratorio > Vías respiratorias; Super > Hogar > Lavandería; Super > Higiene y belleza > Cuidado bucal; Farmacia > Salud sexual > Preservativos |

## Ejecución recomendada

- **Soriana:** GitHub-hosted Actions.
- **Chedraui:** GitHub-hosted Actions con selección obligatoria de Chedraui Selecto México Polanco / tienda 232.
- **Farmacias Guadalajara:** contexto `fg-online`, sin sucursal asignada. En las pruebas del 19 de agosto de 2026, los runners hospedados oficiales de GitHub en Linux, macOS y Windows resolvieron el dominio/Akamai pero no recibieron respuesta HTTP. El scraper clasifica este caso como `NETWORK_UNAVAILABLE` y debe ejecutarse desde una red que sí pueda acceder al sitio oficial. No se utilizan proxies ni mecanismos de evasión.
- **Walmart:** GitHub-hosted Actions reutilizando una sesión Playwright verificada manualmente y guardada como GitHub Secret.
- **No se automatizan CAPTCHAs ni desafíos de identidad.**

La guía de sesión Walmart está en [`HOSTED_SESSION.md`](HOSTED_SESSION.md).

## Estructura principal

```text
scraper/
├── main.py
├── scraper/retailers/
│   ├── soriana.py
│   ├── chedraui.py
│   ├── chedraui_polanco.py
│   ├── chedraui_polanco_api.py
│   ├── farmacias_guadalajara.py
│   ├── walmart.py
│   ├── walmart_persistent.py
│   └── walmart_storage_state.py
├── scripts/
│   ├── export_walmart_session.py
│   ├── walmart_prepare_session.py
│   └── run_all_retailers.py
├── config/
│   ├── locations.yaml
│   ├── soriana/categories.yaml
│   ├── chedraui/categories.yaml
│   ├── farmacias-guadalajara/categories.yaml
│   └── walmart/categories.yaml
└── .github/workflows/
    ├── soriana.yml
    └── full-cloud.yml
```

## Farmacias Guadalajara / catálogo online

Contexto configurado:

```text
id: fg-online
city: Catálogo online
state: Nacional
store: null
store_id: null
postal_code: null
```

Categorías:

- `vias-respiratorias`: Medicina > Respiratorio > Vías respiratorias
- `lavanderia`: Super > Hogar > Lavandería
- `cuidado-bucal`: Super > Higiene y belleza > Cuidado bucal
- `preservativos`: Farmacia > Salud sexual > Preservativos

Ejemplos:

```bash
python main.py --retailer farmacias-guadalajara --category vias-respiratorias --location fg-online
python main.py --retailer farmacias-guadalajara --category lavanderia --location fg-online
python main.py --retailer farmacias-guadalajara --category cuidado-bucal --location fg-online
python main.py --retailer farmacias-guadalajara --category preservativos --location fg-online
```

El scraper recorre el catálogo público, expande el control de "Ver más productos", extrae SKU, marca, producto, precio actual, precio regular, promoción y URL, y conserva `store_context_verified=False` mientras no exista una sucursal configurada. Si la red no puede establecer una respuesta con el dominio oficial, termina con exit code `5`, escribe un diagnóstico y reporta `NETWORK_UNAVAILABLE` en lugar de crear datos parciales o inventados.

Los precios del catálogo online no se atribuyen a una sucursal concreta porque Farmacias Guadalajara indica que precios e inventarios pueden variar según la ubicación seleccionada.

## Chedraui / Polanco

Tienda configurada:

```text
id: chedraui-polanco
store: Chedraui Selecto México Polanco
store_id: 232
postal_code: 11500
city: Miguel Hidalgo
state: CDMX
```

Categorías:

- `higiene-bucal`: Cuidado e higiene personal > Higiene bucal
- `lavanderia`: Supermercado > Limpieza del hogar > Lavandería

Ejemplos:

```bash
python main.py --retailer chedraui --category higiene-bucal --store chedraui-polanco
python main.py --retailer chedraui --category lavanderia --store chedraui-polanco
```

El scraper selecciona Polanco mediante el directorio de tiendas del storefront, valida el contexto antes de escribir filas y recorre exhaustivamente la paginación. Si una página HTML de VTEX queda vacía entre páginas válidas, recupera únicamente ese rango con la misma consulta pública `productSearchV3` generada por el storefront en la misma sesión.

## Walmart / SC Toreo

Tienda configurada:

```text
id: sc-toreo
store: SC Toreo
store_id: 2344
postal_code: 11220
city: Miguel Hidalgo
state: CDMX
```

Categorías:

- `cuidado-bucal`
- `cuidado-de-la-ropa`
- `depilacion-y-rasurado`

### Crear una sesión portable

En una computadora con navegador visible:

```bash
pip install -r requirements.txt
playwright install chromium
python scripts/export_walmart_session.py
```

Completa manualmente cualquier verificación de Walmart y confirma SC Toreo. El script genera localmente:

```text
walmart_session.json
walmart_session.secret.txt
```

Ambos están ignorados por Git y contienen estado sensible.

Copia **todo el contenido** de `walmart_session.secret.txt` a un Repository Secret llamado:

```text
WALMART_SESSION_GZIP_B64
```

Luego ejecuta en GitHub Actions:

```text
Full Scraper - Hosted Session
```

## Ejecución completa

Con un perfil Walmart persistente ya preparado y desde una red que también pueda acceder a Farmacias Guadalajara:

```bash
python scripts/run_all_retailers.py --walmart-profile-dir .walmart_profile
```

El runner incluye Soriana, Chedraui, Farmacias Guadalajara y Walmart, continúa procesando todos los casos aunque uno falle, concentra las categorías exitosas y registra el estado de cada caso en `Resumen`. `NETWORK_UNAVAILABLE` queda diferenciado de `BLOCKED`, `STORE_CONTEXT_ERROR` y otros errores.

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
