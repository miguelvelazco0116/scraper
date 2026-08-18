# Scraper multi-retailer

Base modular para extraer catálogos públicos de retailers de México mediante **Python + Playwright**, generar CSV/Excel y ejecutar pruebas desde GitHub Actions o runners propios.

## Retailers

| Retailer | Estado | Categorías implementadas |
|---|---|---|
| Soriana | Implementado y validado | Cuidado bucal; Cuidado del hogar > Limpiadores; Cuidado del hogar > Limpiadores > Detergentes |
| Walmart | Implementado; GitHub-hosted bloqueado por desafío de identidad | Belleza y cuidado personal > Higiene y cuidado personal > Cuidado bucal; Limpieza del hogar y cuidado personal > Cuidado de la ropa |
| Chedraui | Pendiente | — |

La arquitectura usa un módulo por retailer para no mezclar selectores, navegación, paginación o diagnósticos.

## Estructura

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
│   └── walmart_prepare_session.py
├── config/
│   ├── locations.yaml
│   ├── soriana/categories.yaml
│   └── walmart/categories.yaml
├── tests/
└── .github/
    ├── run/retailer-trigger.txt
    └── workflows/soriana.yml   # workflow multi-retailer
```

## Soriana

Categorías configuradas:

- `cuidado-bucal`: Cuidado bucal
- `limpiadores`: Cuidado del hogar > Limpiadores
- `detergentes`: Cuidado del hogar > Limpiadores > Detergentes

Ejemplos:

```bash
python main.py --retailer soriana --category cuidado-bucal --location cdmx
python main.py --retailer soriana --category limpiadores --location cdmx
python main.py --retailer soriana --category detergentes --location cdmx
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

```text
cuidado-bucal
Belleza y cuidado personal
  └── Higiene y cuidado personal
      └── Cuidado bucal

cuidado-de-la-ropa
Limpieza del hogar y cuidado personal
  └── Cuidado de la ropa
```

URLs de catálogo:

```text
https://www.walmart.com.mx/browse/cuidado-personal/cuidado-bucal/264479_950014
https://www.walmart.com.mx/browse/cuidado-de-la-ropa/3680083
```

### Ejecución sin perfil persistente

```bash
python main.py --retailer walmart --category cuidado-bucal --store sc-toreo
python main.py --retailer walmart --category cuidado-de-la-ropa --store sc-toreo
```

El módulo de Walmart:

- recorre la paginación mediante `?page=N`;
- deduplica por SKU/URL;
- reconoce SKU numérico desde URLs `/ip/.../<id>`;
- exige contexto de tienda antes de etiquetar precios como SC Toreo;
- filtra productos con señal de pickup/recogida cuando la corrida es por tienda;
- guarda screenshot, HTML y metadata de diagnóstico cuando la sesión falla;
- no resuelve CAPTCHAs ni desafíos de identidad, no rota proxies y no falsifica fingerprint.

### Limitación observada en GitHub-hosted runners

La validación en `ubuntu-latest` pudo abrir la página pública de SC Toreo, pero al entrar al catálogo Walmart redirigió a `/blocked` y mostró **"Verifica tu identidad / Mantén presionado"**. El scraper se detiene en ese punto para no automatizar el desafío ni etiquetar datos nacionales como datos de tienda.

### Ejecución recomendada para Walmart: perfil local persistente

En una computadora o runner propio, prepara una sesión normal una sola vez:

```bash
python scripts/walmart_prepare_session.py --profile-dir .walmart_profile
```

Se abrirá Chromium visible. Completa manualmente cualquier verificación que Walmart solicite y confirma **SC Toreo / CP 11220**. Después vuelve a la terminal y presiona ENTER para guardar el perfil.

Luego ejecuta las categorías reutilizando esa sesión:

```bash
python main.py \
  --retailer walmart \
  --category cuidado-bucal \
  --store sc-toreo \
  --profile-dir .walmart_profile

python main.py \
  --retailer walmart \
  --category cuidado-de-la-ropa \
  --store sc-toreo \
  --profile-dir .walmart_profile
```

También puede definirse la variable `WALMART_USER_DATA_DIR` en lugar de `--profile-dir`. Las carpetas `.walmart_profile/` y `walmart_profile/` están ignoradas por Git. Nunca subir cookies, perfiles de navegador o credenciales al repositorio.

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
url
price_raw
```

Los campos específicos que no aplican a un retailer quedan vacíos.

## Instalación local

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
playwright install chromium
```

Para abrir el navegador visible agrega `--headed`.

## GitHub Actions

El workflow actual es multi-retailer y permite elegir `soriana` o `walmart` mediante **Run workflow**. Las corridas disparadas desde ChatGPT usan únicamente `.github/run/retailer-trigger.txt`, con `retailer`, `category` y `location` explícitos.

GitHub-hosted runners funcionan para Soriana en las pruebas realizadas. Para Walmart, si aparece el desafío de identidad descrito arriba, la corrida falla de forma controlada y publica `diagnostics/` como artifact.

## Pruebas

```bash
pytest -q
```
