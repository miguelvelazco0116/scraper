# Scraper multi-retailer

Proyecto modular para extraer catálogos públicos de retailers en México, validar cobertura y precios, y concentrar los resultados en un único Excel.

El proyecto está pensado para ejecutarse localmente en Windows Server con Python/Playwright y, cuando un retailer lo requiere, Google Chrome visible. No se automatizan CAPTCHAs, verificaciones de identidad ni mecanismos para evadir controles de acceso.

## Estado actual

Última actualización: 2 de octubre de 2026.

| Retailer | Runner activo | Categorías configuradas | Última validación conocida | Observaciones |
|---|---:|---|---:|---|
| Soriana | Sí | Cuidado bucal; Limpiadores; Detergentes; Afeitado y depilación para dama; Desodorantes para caballero; Desodorantes para dama | 2,148 registros de categoría | Nuevas rutas de desodorantes validadas: 274 caballero + 196 dama |
| Chedraui | Sí | Higiene bucal; Lavandería | 651 productos | Tienda Chedraui Selecto México Polanco, store_id 232 |
| Farmacias del Ahorro | Sí | Congestión nasal; Preservativos; Enjuagues bucales; Cremas dentales | 288 productos | Catálogo online nacional |
| Farmacias San Pablo | Sí | Descongestionantes; Preservativos; Enjuagues bucales; Pastas dentales | 239 productos | Selenium/Chrome; algunas fichas no exponen SKU o URL estable |
| Ibarra Mayoreo | Sí | Detergentes Abarrotes; Detergentes Marca propia; Dentífricos; Perfumería | 570 productos con precio CAJA | Sólo se guardan presentaciones con precio por caja |
| Bodega Aurrera | Sí | Cuidado bucal; Cuidado de la ropa | 261 productos | Puede solicitar verificación manual; cobertura y precios validados |
| Farmacias Similares | Sí | Aparato respiratorio; Condones | 30 productos | Cobertura 100% en la validación local |
| La Comer | No | Detergentes y suavizantes; Cuidado bucal | Pendiente de validación live | Implementado, aún no activado en el runner principal |
| Walmart | No | Cuidado bucal; Cuidado de la ropa; Depilación y rasurado | Pausado | Sesiones automatizadas pueden ser bloqueadas |
| Farmacias Guadalajara | No | Vías respiratorias; Lavandería; Cuidado bucal; Preservativos | Pausado | Problemas de conectividad/bloqueo en validaciones previas |

> Los conteos anteriores corresponden a las últimas validaciones individuales conocidas. Una corrida completa con todos los retailers activos debe volver a ejecutarse después de cambios relevantes en el código o en los sitios.

## Estado de integración Git

La documentación de esta rama resume el trabajo acumulado del proyecto. Actualmente hay dos líneas que deben sincronizarse antes del siguiente merge general:

- `main` ya contiene las nuevas categorías de Soriana `desodorantes-para-caballero` y `desodorantes-para-dama`, validadas el 2 de octubre de 2026.
- `feature/farmacias-similares` contiene la integración más amplia del stack local: Bodega Aurrera, Ibarra Mayoreo, La Comer, Farmacias Similares, mejoras de Farmacias San Pablo y el runner `run_active_retailers.ps1`.
- El PR #34 sigue abierto como borrador y debe sincronizarse con `main` antes de considerarse la nueva línea estable.

Esto evita confundir funcionalidades ya fusionadas en `main` con funcionalidades implementadas y validadas que todavía viven en ramas de integración.

## Retailers activos

El runner principal procesa actualmente:

```text
Soriana
Chedraui
Farmacias del Ahorro
Farmacias San Pablo
Ibarra Mayoreo
Bodega Aurrera
Farmacias Similares
```

Walmart y Farmacias Guadalajara permanecen en pausa. La Comer está implementado pero pendiente de validación live antes de activarse.

## Ejecución completa

En Windows PowerShell:

```powershell
cd C:\Proyectos\scraper

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

& .\scripts\run_active_retailers.ps1
```

El wrapper ejecuta:

```powershell
python .\scripts\run_all_retailers.py --local-browser
```

El modo `--local-browser` usa Chrome visible para los retailers que lo requieren.

### Importante para Bodega Aurrera

Bodega Aurrera puede mostrar una verificación de identidad durante la navegación. Si aparece:

1. Completa manualmente la verificación en Chrome.
2. No cierres la ventana.
3. El scraper continuará cuando el sitio libere la sesión.

El scraper no resuelve ni evade esa verificación automáticamente.

## Output consolidado

La corrida principal genera:

```text
output\concentrado_scraper.xlsx
```

Hojas:

- `Concentrado`
- `Resumen`

Columnas principales:

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

Los logs de la corrida completa se guardan en:

```text
diagnostics\run_all
```

## Bodega Aurrera

### Cuidado bucal

```text
Departamento: Belleza y cuidado personal
Categoría:    Higiene y cuidado personal
Subcategoría: Cuidado bucal
category_id:  cuidado-bucal
```

URL:

```text
https://despensa.bodegaaurrera.com.mx/browse/higiene-personal-y-belleza/cuidado-bucal/10_1004
```

Última validación:

```text
Productos únicos:        120
SKU:                     120 / 120
Precio actual:           120 / 120
URL:                     120 / 120
Fuentes visitadas:       1 / 1
pagination_verified:     True
Errores de precio:       0
Bloqueo:                 Sí, resuelto manualmente
```

### Cuidado de la ropa

```text
Departamento: Limpieza del hogar y cuidado de la ropa
Categoría:    Cuidado de la ropa
category_id:  cuidado-de-la-ropa
```

URL:

```text
https://www.bodegaaurrera.com.mx/content/cuidado-de-la-ropa/3680083
```

La validación no inventa páginas `?page=N`. Descubre las secciones reales de la landing page, entra en ellas, sigue las páginas explícitas que publica el sitio y carga contenido hasta que el número de productos se estabiliza.

Última validación:

```text
Productos únicos:        141
SKU:                     141 / 141
Precio actual:           141 / 141
URL:                     141 / 141
Fuentes descubiertas:    6
Fuentes visitadas:       6
Páginas explícitas:      8
pagination_verified:     True
Precio actual validado:  141 / 141
Precio regular validado: 51 / 51
Errores de precio:       0
```

Test dedicado:

```powershell
& .\scripts\test_bodega_aurrera.ps1
```

Output:

```text
output\bodega_aurrera_test.xlsx
```

Hojas de auditoría:

- `Concentrado`
- `Resumen`
- `Fuentes`
- `Extraccion`
- `ValidacionPrecios`

## Farmacias Similares

Contexto:

```text
id: similares-online
city: Catálogo online
state: Nacional
store: Farmacias Similares online
```

### Aparato respiratorio

```text
Categoría:    Aparato respiratorio
Subcategoría: Aparato respiratorio
category_id:  aparato-respiratorio
URL:          https://www.farmaciasdesimilares.com/aparato-respiratorio/aparato-respiratorio
```

Última validación:

```text
Target publicado:         21
Links descubiertos:       21
Productos:                21
SKU:                      21 / 21
Precio actual:            21 / 21
Precio regular:           21 / 21
URL:                      21 / 21
discovery_complete:       True
Sin precio:               0
Errores de detalle:       0
Errores de promoción:     0
Bloqueo:                  No
Páginas visitadas:        2
```

### Salud sexual > Condones

```text
Categoría:    Salud sexual
Subcategoría: Condones
category_id:  condones
URL:          https://www.farmaciasdesimilares.com/salud-sexual/condones
```

Última validación:

```text
Target publicado:         9
Links descubiertos:       9
Productos:                9
SKU:                      9 / 9
Precio actual:            9 / 9
Precio regular:           9 / 9
URL:                      9 / 9
discovery_complete:       True
Sin precio:               0
Errores de detalle:       0
Errores de promoción:     0
Bloqueo:                  No
Páginas visitadas:        1
```

El scraper visita cada ficha de producto y obtiene:

- SKU desde `Referencia:`.
- `price_regular` desde `De $...` cuando existe.
- `price_current` desde `Por $...` cuando existe.
- Promoción desde `Ahorra $...`.
- En productos sin promoción, `price_current` y `price_regular` quedan iguales.

Se espera la hidratación del PDP antes de fijar el precio para evitar capturar únicamente el precio regular cuando una promoción aparece después del primer render.

Test dedicado:

```powershell
& .\scripts\test_farmacias_similares.ps1
```

Output:

```text
output\farmacias_similares_test.xlsx
```

## Ibarra Mayoreo

Contexto:

```text
id: ibarra-online
city: Catálogo online
state: Nacional
store: Ibarra Mayoreo online
```

Categorías:

```text
detergentes-lavatrastes-jab-abarrotes
detergentes-lavatrastes-jab-marca-propia
dentifricos-abarrotes
perfumeria-abarrotes
```

Regla crítica: **sólo se guarda el precio de presentación CAJA**. Un producto sin precio de caja se registra en diagnóstico pero no se agrega al concentrado.

Última validación:

```text
Detergentes Abarrotes:      152
Detergentes Marca propia:     2
Dentífricos:                 86
Perfumería:                 330
Total con precio CAJA:      570
Errores de parseo:            0
```

Test dedicado:

```powershell
& .\scripts\test_ibarra_mayoreo.ps1
```

Output:

```text
output\ibarra_mayoreo_test.xlsx
```

## Farmacias del Ahorro

Contexto:

```text
id: fahorro-online
city: Catálogo online
state: Nacional
```

Categorías activas:

```text
congestion-nasal
preservativos
enjuagues-bucales
cremas-dentales
```

El scraper utiliza el catálogo online y conserva SKU, nombre, marca, URL, precio actual y precio anterior cuando está disponible.

## Farmacias San Pablo

Contexto:

```text
id: san-pablo-online
city: Catálogo online
state: Nacional
```

Categorías activas:

```text
descongestionantes
preservativos
enjuagues-bucales
pastas-dentales
```

La implementación usa Chrome/Selenium. Algunas tarjetas no exponen un SKU o URL estable; por eso el concentrado conserva filas válidas aun cuando ambos identificadores no estén presentes.

## Chedraui

Ubicación validada:

```text
id: chedraui-polanco
store: Chedraui Selecto México Polanco
store_id: 232
postal_code: 11500
city: Miguel Hidalgo
state: CDMX
```

Categorías:

```text
higiene-bucal
lavanderia
```

## Soriana

Categorías configuradas en el estado acumulado del proyecto:

```text
cuidado-bucal
limpiadores
detergentes
afeitado-depilacion-dama
desodorantes-para-caballero
desodorantes-para-dama
```

Jerarquías nuevas:

```text
desodorantes-para-caballero
Cuidado personal y belleza > Talcos y desodorantes > Desodorantes para caballero

desodorantes-para-dama
Cuidado personal y belleza > Talcos y desodorantes > Desodorantes para dama
```

Validación live del 2 de octubre de 2026:

```text
Desodorantes para caballero: 274 productos únicos
Desodorantes para dama:       196 productos únicos
Cobertura vs total publicado: completa en ambas categorías
Unit tests:                   SUCCESS
Scraper:                      SUCCESS
Workflow en main:             SUCCESS
```

Ambas rutas reutilizan el paginador exhaustivo existente de Soriana; no fue necesario crear un motor adicional. Los resultados se normalizan al mismo esquema del concentrado.

## La Comer

Implementado pero todavía no activado en la corrida principal.

Categorías configuradas:

```text
detergentes-suavizantes
cuidado-bucal
```

Antes de activarlo debe completarse una validación live de catálogo, tienda/contexto y precios.

## Retailers en pausa

### Walmart

Walmart permanece en pausa. El proyecto conserva su implementación, perfil persistente y utilidades de sesión, pero las últimas pruebas mostraron bloqueo de la sesión automatizada.

No se automatizan verificaciones de identidad ni se intenta evadir la protección del sitio.

### Farmacias Guadalajara

Permanece en pausa por problemas de conectividad/bloqueo observados en las validaciones previas.

## Tests dedicados

```powershell
& .\scripts\test_bodega_aurrera.ps1
& .\scripts\test_farmacias_similares.ps1
& .\scripts\test_ibarra_mayoreo.ps1
```

Soriana se valida desde el CLI principal y desde el workflow dedicado:

```powershell
python .\main.py --retailer soriana --category desodorantes-para-caballero --location cdmx
python .\main.py --retailer soriana --category desodorantes-para-dama --location cdmx
```

Para las pruebas generales de Python:

```powershell
pytest -q
```

## Criterios de calidad

Siempre que la estructura del retailer lo permita, las validaciones verifican:

- Cobertura del total publicado por la categoría.
- Recorrido de todas las páginas/fuentes reales.
- Deduplicación por SKU y URL.
- Cobertura de SKU.
- Cobertura de precio actual.
- Cobertura de URL.
- Consistencia de precio promocional frente a precio regular.
- Conteo de productos sin precio.
- Errores de detalle/parseo.
- Detección explícita de bloqueos y desafíos de identidad.

Los resultados incompletos no deben interpretarse automáticamente como catálogos completos.

## Estructura

```text
config/
  <retailer>/categories.yaml
scraper/
  retailers/
scripts/
diagnostics/
output/
main.py
```

Cada retailer mantiene su configuración y extractor por separado, mientras `main.py` normaliza la salida hacia el mismo esquema consolidado.


## Historial reciente

### 2 de octubre de 2026

- Se agregaron a Soriana las rutas de desodorantes para caballero y dama.
- Se añadieron pruebas de jerarquía y descubrimiento para ambas categorías.
- Se actualizó la documentación de Soriana.
- Se validaron 274 productos de caballero y 196 de dama contra los totales publicados.
- El workflow final sobre `main` concluyó correctamente.

### 1 de octubre de 2026

- Se integró el scraper de Farmacias Similares con visita a PDP, SKU desde `Referencia:` y validación de precios promocionales.
- Se validaron 21/21 productos de Aparato respiratorio y 9/9 de Condones.
- Se incorporaron Bodega Aurrera, Ibarra Mayoreo y La Comer en la rama de integración.
- Se reforzó Farmacias San Pablo para ejecución local con Chrome/Selenium.
- Se creó el runner local `run_active_retailers.ps1`.
- Se documentaron criterios comunes de cobertura, paginación, deduplicación y calidad de precios.

## Próximo paso de integración

Antes del siguiente release del proyecto:

1. Sincronizar `feature/farmacias-similares` con `main` para incorporar las dos nuevas categorías de Soriana.
2. Ejecutar `pytest -q`.
3. Ejecutar el test completo de retailers activos en Windows Server.
4. Revisar `output\concentrado_scraper.xlsx` y los diagnósticos por retailer.
5. Fusionar el PR #34 cuando el consolidado completo quede validado.
