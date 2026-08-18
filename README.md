# Scraper multi-retailer

Base modular para extraer catálogos públicos de retailers de México mediante **Python + Playwright**, generar un único Excel consolidado y ejecutar pruebas desde GitHub Actions o desde una VM cloud dedicada.

## Retailers

| Retailer | Estado | Categorías implementadas |
|---|---|---|
| Soriana | Implementado y validado | Cuidado bucal; Cuidado del hogar > Limpiadores; Cuidado del hogar > Limpiadores > Detergentes; Cuidado personal > Afeitado y depilación > Afeitado y depilación para dama |
| Walmart | Implementado; GitHub-hosted bloqueado por desafío de identidad | Belleza y cuidado personal > Higiene y cuidado personal > Cuidado bucal; Limpieza del hogar y cuidado personal > Cuidado de la ropa; Belleza y cuidado personal > Depilación y rasurado |
| Chedraui | Pendiente | — |

La arquitectura usa un módulo por retailer para no mezclar selectores, navegación, paginación o diagnósticos.

## Ejecución recomendada

- **Soriana:** puede ejecutarse en GitHub-hosted runners.
- **Walmart:** debe ejecutarse en una **VM Linux cloud dedicada** con perfil persistente de Chromium para SC Toreo.
- **No se requiere usar una PC personal como runner.**
- La VM cloud puede administrarse mediante Azure Bastion/RDP y ejecutar GitHub Actions como servicio usando `xvfb-run`.

La guía completa está en [`CLOUD_RUNNER.md`](CLOUD_RUNNER.md).

## Estructura principal

```text
scraper/
├── main.py
├── scraper/
│   ├── config.py
│   ├── parsers.py
│   └── retailers/
│       ├── soriana.py
│       ├── walmart.py
│       └── walmart_persistent.py
├── scripts/
│   ├── walmart_prepare_session.py
│   ├── setup_cloud_runner_ubuntu.sh
│   ├── setup_walmart_session_linux.sh
│   └── run_all_retailers.py
├── config/
│   ├── locations.yaml
│   ├── soriana/categories.yaml
│   └── walmart/categories.yaml
├── tests/
└── .github/
    ├── run/retailer-trigger.txt
    └── workflows/
        ├── soriana.yml
        └── full-cloud.yml
```

## Soriana

Categorías configuradas:

- `cuidado-bucal`: Cuidado bucal
- `limpiadores`: Cuidado del hogar > Limpiadores
- `detergentes`: Cuidado del hogar > Limpiadores > Detergentes
- `afeitado-depilacion-dama`: Cuidado personal > Afeitado y depilación > Afeitado y depilación para dama

Ejemplo:

```bash
python main.py --retailer soriana --category cuidado-bucal --location cdmx
```

## Walmart México

Tienda configurada:

```text
id: sc-toreo
store: SC Toreo
store_id: 2344
postal_code: 11220
city: Miguel Hidalgo
state: CDMX
```

Categorías configuradas:

- `cuidado-bucal`
- `cuidado-de-la-ropa`
- `depilacion-y-rasurado`

El módulo de Walmart:

- recorre paginación;
- deduplica SKU/URL;
- valida contexto de tienda;
- conserva un perfil persistente de Chromium en la VM cloud;
- no intenta resolver CAPTCHAs ni desafíos de identidad;
- se detiene como `BLOCKED` si Walmart vuelve a solicitar verificación.

## VM cloud

Preparación base en Ubuntu:

```bash
chmod +x scripts/setup_cloud_runner_ubuntu.sh
./scripts/setup_cloud_runner_ubuntu.sh
```

Después conecta la VM por Azure Bastion/RDP y prepara una sesión de SC Toreo:

```bash
chmod +x scripts/setup_walmart_session_linux.sh
./scripts/setup_walmart_session_linux.sh
```

El perfil predeterminado queda fuera del workspace de GitHub:

```text
$HOME/scraper_profiles/walmart_sc_toreo
```

Registra la VM como GitHub self-hosted runner Linux x64 con la etiqueta adicional:

```text
cloud-scraper
```

Luego ejecuta en GitHub Actions:

```text
Full Scraper - Cloud VM
```

## Output

Todas las categorías exitosas se concentran en:

```text
output/concentrado_scraper.xlsx
```

Hojas:

- `Concentrado`
- `Resumen`

## Campos de salida

```text
scrape_timestamp
retailer
city
state
postal_code
store
store_id
department
category
subcategory
sub_subcategory
category_id
sku
brand
product
price_current
price_regular
promotion
pickup_available
store_context_verified
store_context_method
url
price_raw
```

## Pruebas

```bash
pytest -q
```
