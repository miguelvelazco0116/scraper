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
- `sku`: **certificado desde el texto visible `Sku:` en la ficha
  individual del producto (PDP)**. Una URL de imagen o un atributo en la
  tarjeta solamente son candidatos de diagnóstico; por sí solos **NO**
  certifican el SKU. Nunca se genera un código desde el slug del nombre.
- El probe visita hasta 3 PDP de la muestra y verifica el SKU visible.
  El capture consulta la ficha de CADA producto, únicamente después de
  completar la carga de la categoría. Sin prueba visible marca PARTIAL.
- Precio promocional `De $X Para/Por $Y` y precio regular.
- Clics en **Mostrar más**, esperando hasta que aparezcan enlaces a
  productos nuevos. El probe pulsa una vez y exige tarjetas diferentes,
  para no declarar aptitud sin verificar paginación.
- Una espera fija de 950 ms no garantiza que el catálogo SPA haya cambiado.
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
- `output/pilots/farmatodo/catalogo_online.xlsx` → `Concentrado`,
  `Resumen` y **`SkuAudit`**, con URL, SKU, fuente `pdp_visible_sku` y
  confirmación booleana por producto. Los parciales no se consideran
  completos ni pueden reutilizarse como tales.
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

## Actualización 27/oct/2026 — evidencia real del probe
- Respiratorios: 6 visibles / 460 publicados, 0 SKU de tarjeta.
- Cuidado bucal: 6 / 169, 0 SKU de tarjeta.
- Higiene personal: 6 / 140, 0 SKU de tarjeta.
- En PDP pública, Colgate Total Prevención Activa Antisarro 150 ml muestra
  `Sku: 7509546653518`, precio previo $145.00 y oferta $108.50.
- El probe actualizado abre hasta tres fichas y exige 3/3 SKU PDP
  verificados, además de un clic exitoso a **Mostrar más**.
- Si el SKU falla: `NEEDS_SKU_REVIEW`; si el avance falla tras SKU
  confirmado: `NEEDS_PAGINATION_REVIEW`.
- Solo con ambos checks el resultado será `PROBE`. La captura completa
  certifica cobertura 90%-100% más SKU PDP, URL, precios y ausencia de
  localidad inventada.

Comandos (rama Fase 6, no master):
```powershell
git pull --ff-only
python -m pytest tests/test_phase6_pilot.py tests/test_yza_pilot.py tests/test_farmatodo_pilot.py -v
& .\scripts\run_phase6_pilot.ps1 -Mode probe -Retailer farmatodo
```
No ejecutar captura completa hasta los tres probes correctos.
