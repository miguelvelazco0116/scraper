# Chedraui: validación de disponibilidad desde la tarjeta de producto

## Señales confirmadas

La captura visual del 26 de octubre muestra una tarjeta Chedraui
con CTA naranja **Agregar** y productos con precio visible. Para esos
productos, un botón **Agregar habilitado y visible en su propia tarjeta**
constituye evidencia de disponibilidad para compra en la sesión.

No tenemos una captura verificada del botón de agotado de Chedraui.
Por tanto, **la ausencia de Agregar no equivale a agotado**.

| Evidencia | availability_status | is_available |
|---|---|---|
| CTA visible y habilitado: Agregar / Agregar + | AVAILABLE | True |
| Inventario VTEX AvailableQuantity > 0 | AVAILABLE | True |
| Inventario VTEX AvailableQuantity = 0 | UNAVAILABLE | False |
| Texto de producto explícito Agotado / Sin stock | UNAVAILABLE | False |
| Botón ausente/oculto/deshabilitado sin prueba adicional | UNKNOWN | None |
| Señales DOM contradictorias dentro de una misma tarjeta | UNKNOWN | None |

## Arquitectura conservadora

- El capturador `scraper/retailers/chedraui.py` identifica un bloque de
  producto sin ascender a un contenedor con varios enlaces de producto y
  extrae botones habilitados o deshabilitados de **esa tarjeta**.
- `scraper/retailers/chedraui_availability.py` interpreta los controles.
- `scraper/retailers/chedraui_polanco_api.py` **nunca sobrescribe una
  cantidad VTEX explícita**. Solo los productos `UNKNOWN` pueden
  complementarse con una señal DOM del mismo URL, categoría y tienda
  verificada. Si hay URLs duplicadas en HTML no se infiere stock.
- `availability_raw` indica si la fuente fue
  `productSearchV3 AvailableQuantity=...` o `dom_cta: Agregar`.
- Las filas no se filtran por agotados: los quiebres de stock también son
  datos útiles para inteligencia competitiva.

## Testing por terminal (solo sin otro scraper activo)

```powershell
cd C:\Proyectos\scraper
& C:\Proyectos\venvs\scraper\Scripts\Activate.ps1
git status --short
git fetch origin
git switch --track origin/feat/chedraui-dom-availability-20261026
python -m pytest tests/test_chedraui_dom_availability.py tests/test_chedraui_availability.py -v
```

En caso de tener la rama ya creada localmente, usar `git switch
feat/chedraui-dom-availability-20261026` y `git pull --ff-only`.

Tras pruebas PASS, probar con **una categoría y una tienda** en una
sesión controlada:

```powershell
& .\scripts\test_chedraui.ps1 -Category higiene-bucal -Store chedraui-polanco -Headed
```

Esta última orden usa el runner existente y **puede escribir sus archivos
habituales de captura/checkpoint**; no es una prueba de solo lectura.
Es recomendable respaldar esos archivos antes de correrla si son importantes.

## Criterios antes de fusionar

1. Sin regresiones en las reglas de contexto de tienda, fechas, SKU,
   paginación, precio y cobertura existentes.
2. Disponibilidad por producto y evidencia `availability_raw` verificable.
3. Los productos con ausencia de evidencia permanecen `UNKNOWN`.
4. Confirmar en HTML/Chrome que los botones se identifican por tarjeta
   y no hay contaminación desde tarjetas vecinas.
5. La cantidad VTEX explícita tiene preferencia sobre los botones DOM.
6. Auditoría del master intacta; no se cambia el número de categorías,
   retailers o fases.

**El cambio queda en PR borrador hasta pasar pytest y validación real en Windows.**
