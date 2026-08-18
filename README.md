# Scraper multi-retailer

Base modular para extraer catálogos públicos de retailers de México mediante **Python + Playwright**, generar un único Excel consolidado y ejecutar pruebas desde GitHub Actions.

## Retailers

| Retailer | Estado | Categorías implementadas |
|---|---|---|
| Soriana | Implementado y validado | Cuidado bucal; Cuidado del hogar > Limpiadores; Cuidado del hogar > Limpiadores > Detergentes; Cuidado personal > Afeitado y depilación > Afeitado y depilación para dama |
| Walmart | Implementado; requiere una sesión verificada portable para SC Toreo | Belleza y cuidado personal > Higiene y cuidado personal > Cuidado bucal; Limpieza del hogar y cuidado personal > Cuidado de la ropa; Belleza y cuidado personal > Depilación y rasurado |
| Chedraui | Pendiente | — |

## Ejecución recomendada

- **Soriana:** GitHub-hosted Actions.
- **Walmart:** GitHub-hosted Actions reutilizando una sesión Playwright verificada manualmente y guardada como GitHub Secret.
- **No se requiere VM ni self-hosted runner.**
- **No se automatizan CAPTCHAs ni desafíos de identidad.** Si la sesión expira, se renueva manualmente y se actualiza el Secret.

La guía está en [`HOSTED_SESSION.md`](HOSTED_SESSION.md).

## Estructura principal

```text
scraper/
├── main.py
├── scraper/retailers/
│   ├── soriana.py
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
│   └── walmart/categories.yaml
└── .github/workflows/
    ├── soriana.yml
    └── full-cloud.yml
```

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

El workflow reconstruye la sesión sólo dentro del runner temporal, ejecuta todas las categorías y borra el archivo al terminar.

## Ejecución individual con storage state

```bash
python main.py \
  --retailer walmart \
  --category cuidado-bucal \
  --store sc-toreo \
  --storage-state walmart_session.json
```

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
