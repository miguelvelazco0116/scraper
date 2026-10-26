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


## Nuevas URLs solicitadas (26 de octubre de 2026)

1. `https://www.chedraui.com.mx/farmacia/medicamentos/respiratorios`:
   la categoría `respiratorios` **ya existe y permanece activa** en
   `config/chedraui/categories.yaml` para las cuatro tiendas, sin duplicar.
2. `https://www.chedraui.com.mx/farmacia/equipo-y-botiquin-medico`:
   añadida como `equipo-y-botiquin-medico` en
   `config/chedraui/candidate_categories.yaml`. Se puede probar
   individualmente con `-Pilot`, pero **NO** se agrega aún a los 20
   casos de Fase 1 ni a `current_master.json`.

### Primera captura de Equipo y botiquín médico (CLI aislada)

Sin ninguna corrida activa, con la rama `feat/chedraui-dom-availability-20261026`
y después de pasar las pruebas, ejecutar:

```powershell
& .\scripts\test_chedraui.ps1 -Category equipo-y-botiquin-medico -Store chedraui-polanco -Pilot -Headed
```

El sitio publicó 653 productos al consultar esta URL; dicho conteo NO es
una expectativa fija. Debe auditarse de nuevo por tienda durante el test,
manteniendo >=90% de cobertura, precios cuando sean obligatorios,
SKU/URL completos, paginación íntegra y tienda Polanco verificada (232,
CP 11500). El comportamiento de botón Agregar/AvailableQuantity se prueba
con el mismo cambio de disponibilidad descrito arriba.

Archivos piloto aislados:
- `output/pilots/chedraui/chedraui-polanco/equipo-y-botiquin-medico_test.xlsx`
- Respaldos anteriores: `output/pilots/chedraui/backups/`

En modo `-Pilot` NO se leen ni escriben `output/chedraui_polanco_test.xlsx`,
ni `output/checkpoints_validos/chedraui_cases`, ni el master.
No utilizar `-Category full` / `-Category candidates` mientras haya
categorías candidatas, para evitar una certificación masiva accidental.

Cuando termine de validar en Polanco, repetir con Tepeyac, Cancún Kabah
y Puebla Centro Sur antes de decidir su promoción a Fase 1. Promover una
sexta categoría en las cuatro tiendas elevaría Chedraui de 20 a 24 casos
y Fase 1 de 30 a 34 casos; la integración y auditoría deben modificarse
con respaldo del master, **no ahora**.

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
