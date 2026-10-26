# Farmatodo México — piloto de catálogo online sin validación postal

## Alcance solicitado
- `https://www.farmatodo.com.mx/medicamentos/respiratorios`
- `https://www.farmatodo.com.mx/cuidado-personal-y-belleza/cuidado-bucal`
- `https://www.farmatodo.com.mx/cuidado-personal-y-belleza/higiene-personal`

**Sin comprobación de CP por instrucción del proyecto.** El sitio muestra
"Ingresa un Código Postal", pero el piloto **no lo establece ni verifica**:
`postal_code=NULL`, `store_id=NULL`, `store_context_verified=False`,
`store_context_method=PUBLIC_ONLINE_CATALOGUE_NO_POSTAL_VERIFICATION`.
Los precios pertenecen al **catálogo genérico online**, no se certifican
por región, ubicación, tienda ni inventario local.

## Tecnología y controles

- Google Chrome con Playwright (navegador independiente, o CDP opcional).
- URL de producto terminada en `/p`, título y precio actuales.
- `sku`: atributo visible del producto o candidato numérico de imagen.
  Nunca inventar SKU desde el slug. Verificar en una PDP que el EAN extraído
  coincide con el texto "Sku:" antes de aceptar producción.
- Precio promocional `De $X Para/Por $Y` y precio regular.
- Clics en **Mostrar más**, sin suponer número fijo de productos.
- 3 intentos por categoría; no se avanza a la siguiente antes de agotar los
  intentos de la categoría actual. Las categorías COMPLETE del mismo día
  se reutilizan sin volver a visitar el sitio.
- Validación strict: cobertura 90%-100% del total publicado, SKU+URL+precio
  completos; si no se comprueba el total, `PARTIAL`. El contador de catálogo
  puede variar durante el día.
- No se declara disponible por tener precio. Solo se captura disponibilidad
  con indicación textual expresa. Sin señal → `UNKNOWN`.

## Prueba en Windows
Con ningún scraper ni cambio local en curso:

```powershell
cd C:\Proyectos\scraper
& C:\Proyectos\venvs\scraper\Scripts\Activate.ps1
git status --short
git fetch origin
git switch --track origin/feat/farmatodo-catalogue-cli-20261026
python -m pytest tests/test_farmatodo_pilot.py -v
```

Si la rama ya existe, usar `git switch feat/farmatodo-catalogue-cli-20261026`
y `git pull --ff-only` (sin resetear cambios del usuario).

Hacer primero diagnósticos que NO generan Excel:

```powershell
& .\scripts\test_farmatodo.ps1 -Probe -Category cuidado-bucal -Headed
& .\scripts\test_farmatodo.ps1 -Probe -Category higiene-personal -Headed
& .\scripts\test_farmatodo.ps1 -Probe -Category respiratorios -Headed
```

Después de verificar selectores, precios y SKU:

```powershell
& .\scripts\test_farmatodo.ps1 -Category all -Headed
```

El runner imprime `COMPLETE/PARTIAL`. Si el navegador no carga la
primera categoría Respiratorios, **no** asumir que la URL es válida:
se documentará en el diagnóstico como `PARTIAL` para revisar la ruta.

## Archivos
- `output/pilots/farmatodo/catalogo_online.xlsx` → Consolidado
  y Resumen de tres categorías, incluidos parciales.
- `diagnostics/farmatodo/last_run.json`
- `diagnostics/farmatodo/probe_<category>_<date>.json`
- Respaldos anteriores del archivo piloto en `diagnostics/farmatodo/`.

**No modifica** `output/checkpoints_validos/current_master.json`, master,
fases 1–5, auditor, `EXPECTED`, ni Streamlit.

## Promoción productiva posterior

Tras constatar cobertura real, EAN/SKU y precios, crear un cambio adicional
para incluir Farmatodo como nuevo retailer y 3 casos en la fase que se
acuerde, con fuentes, integrador, auditoría, respaldos y pruebas del panel.
No declarar existencias por CP hasta que se implemente tal certificación.
