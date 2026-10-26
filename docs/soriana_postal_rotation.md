# Soriana — rotación postal verificable (piloto por terminal)

## Objetivo
Rotar **83420 → 44220 → 54030 → 97245**. Cada CP es una sesión
de entrega que debe quedar aplicada realmente en la web antes de capturar.

La rotación produce **cuatro Excel independientes por CP** y nunca mezcla
precios/stock de zonas distintas. No implica un ID de sucursal: se
conserva `store_id=NULL`, `store_context_verified=False`, pero el resumen
registra `postal_context_verified=True` con evidencia visual si pasa.

## Alcance

Por defecto, un caso de prueba por CP de la categoría `cuidado-bucal`.
Se puede optar por `-Category all` para las seis categorías productivas
de Soriana: un máximo de **4 CP × 6 categorías = 24 casos** en el piloto.
La categoría nueva `gastrointestinales` del PR previo también se puede
seleccionar explícitamente, pero no se incluye en `all` hasta su
certificación productiva.

**No cambiar todavía** el contrato de Fase 2 ni su master, auditoría,
checkpoints o Streamlit. Las pruebas no evaden verificaciones del sitio.

## Preflight en Windows

Con capturas detenidas y `git status --short` limpio:

```powershell
cd C:\Proyectos\scraper
& C:\Proyectos\venvs\scraper\Scripts\Activate.ps1
git fetch origin
git switch --track origin/feat/soriana-zip-rotation-20261026
python -m pytest tests/test_soriana_cp_rotation.py -v
```

Si la rama ya existe usar `git switch feat/soriana-zip-rotation-20261026`
y `git pull --ff-only`.

En Chrome, abrir `https://www.soriana.com/` y habilitar
`chrome://inspect/#remote-debugging` → Allow remote debugging.

Primero **un CP, una categoría**:

```powershell
& .\scripts\test_soriana_cp_rotation.ps1 -PostalCode 83420 -Category cuidado-bucal
```

Si el sitio no confirma el CP visiblemente fuera de formularios,
la prueba falla, mantiene el producto fuera de certificación
y genera evidencia para ajustar selectores en la UI real.

Luego rotación de una categoría en cuatro CP:

```powershell
& .\scripts\test_soriana_cp_rotation.ps1 -PostalCode all -Category cuidado-bucal
```

Cuando funcione la ubicación en cuatro CP, ejecuta seis categorías:

```powershell
& .\scripts\test_soriana_cp_rotation.ps1 -PostalCode all -Category all
```

## Resultados
- `output/pilots/soriana_cp_rotation/soriana_83420.xlsx`
- `output/pilots/soriana_cp_rotation/soriana_44220.xlsx`
- `output/pilots/soriana_cp_rotation/soriana_54030.xlsx`
- `output/pilots/soriana_cp_rotation/soriana_97245.xlsx`
- `diagnostics/soriana_cp_rotation/rotation_<YYYY-MM-DD>.json`

Excel por CP: hojas `Concentrado` y `Resumen`.
Los registros identifican la zona, disponibilidad (AVAILABLE/UNAVAILABLE/UNKNOWN),
precio, SKU, categoría, fecha, conteo objetivo y cobertura.
Los parciales nunca se reutilizan como categorías certificadas.
Los `COMPLETE` del mismo día y mismo CP sí pueden reutilizarse.

## Requisitos para COMPLETE
- CP aplicado y visible en zona persistente (no basta escribirlo).
- Reconteo y cobertura 90%-100% del objetivo visible.
- Paginación completa.
- Cobertura mínima 90% de SKU, precios, URL y disponibilidad explícita.
- Nunca inventar tienda ni asignar los productos de una zona a otra.
- Tres intentos por categoría y CP antes de continuar.

**Por probar en Windows**: selector postal real y persistencia tras navegar
a páginas 2+, disponibilidad por tarjeta y calidad de los cuatro CP.

## 26/10 · Corrección a partir de capturas de la interfaz real

El primer CLI con CP 83420 terminó en PARTIAL / 0 productos porque el
selector postal no se reconocía; no porque se hubieran agotado productos.

Las capturas adjuntas permiten definir dos estados:
- Antes de aplicar CP: modal "Elige la mejor opción de entrega para ti",
  "Envío a domicilio", entrada con valor o placeholder como 03100,
  botón rojo "Buscar".
- Después de aplicar CP: CP de cinco dígitos junto al ícono de bolsa
  amarilla en el encabezado superior izquierdo. La imagen de muestra
  mostraba 52164, que NO es 83420 y por tanto debe rechazarse.

El código actualizado:
1. Localiza la fila de CP por el botón Buscar y su textbox vecino,
   aunque el campo no tenga placeholder descriptivo.
2. Sustituye el CP mostrado por el solicitado y hace clic en Buscar.
3. Espera un máximo de varios segundos a que el encabezado refleje
   exactamente el CP solicitado. El valor del textbox por sí solo NO cuenta.
4. Vuelve a verificar el CP después de recargar y en todas las páginas.
5. Guarda observaciones del modal y del CP anterior en
   diagnostics/soriana_cp_rotation/<CP>/<category>/cp_attempt_<n>.json
   además de capturas/HTML de diagnóstico.

Actualizar la rama antes de reintentar:

    git status --short
    git pull --ff-only
    python -m pytest tests/test_soriana_cp_rotation.py -v
    & .\scripts\test_soriana_cp_rotation.ps1 -PostalCode 83420 -Category cuidado-bucal

No lanzar los cuatro CP ni Fase 6 hasta demostrar que 83420 se aplica
realmente, con producto y disponibilidad capturados.

## Resultado de rotación: 26 de octubre, cuidado-bucal

La primera corrida real entregó:
- 83420: COMPLETE, 273 productos, 192 AVAILABLE, 81 UNAVAILABLE.
- 44220: COMPLETE, 338 productos, 210 AVAILABLE, 128 UNAVAILABLE.
- 54030: intento 1 CP confirmado pero 0 tarjetas; intentos 2-3
  bloqueados por espera temprana de .product-tile.js-product-card.
- 97245: tres intentos bloqueados por esa misma espera temprana.

**Corrección:** en el runner de rotación se eliminó
navigate_same_manual_session, porque esperaba productos ANTES de aplicar
el siguiente CP. Ahora se selecciona y verifica el CP primero; luego
se navega a la categoría y se espera explícitamente a las tarjetas.
Si el sitio devuelve la categoría sin tarjetas, se repite la carga
una vez con el mismo CP, conservando PARTIAL si sigue vacío.
Cada falla registra diagnóstico del encabezado/DOM sin alterar master.

Para retomar sin repetir los dos CP completos:

    git pull --ff-only
    python -m pytest tests/test_soriana_cp_rotation.py -v
    & .\scripts\test_soriana_cp_rotation.ps1 -PostalCode 54030 -Category cuidado-bucal

Después de validar 54030:

    & .\scripts\test_soriana_cp_rotation.ps1 -PostalCode 97245 -Category cuidado-bucal

Solo si ambos son COMPLETE, revalidar rotación completa usando
-PostalCode all. El caché debe reutilizar 83420 y 44220
por CP y fecha, sin borrarlos.
