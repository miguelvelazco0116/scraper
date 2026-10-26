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
