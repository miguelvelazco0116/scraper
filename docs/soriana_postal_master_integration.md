# Soriana — integración controlada al master por código postal

## Objetivo y alcance

Los archivos `output/pilots/soriana_cp_rotation/soriana_<CP>.xlsx`
provenientes de los CP `83420`, `44220`, `54030` y `97245` contienen
seis categorías por CP. La captura del **27/10/2026** pasó auditoría local:
24/24 COMPLETE y 8,043 productos, sin mezclar zonas.

La Fase 2 original representa **6 categorías** de Soriana, no 24 casos. Esta
integración reemplaza **únicamente** las filas y los casos Resumen de Soriana
por 24 combinaciones CP/categoría. Las demás tiendas y fases se conservan,
incluidas Farmacias Guadalajara, Chedraui y Farmacias YZA.

**Grano de datos:** `(retailer, postal_code, category_id, sku, url)`.
El mismo SKU en dos CP diferentes se mantiene dos veces: puede tener
precios/disponibilidad distintos. `postal_code` es una zona de entrega,
NO un ID de tienda; `store_id=NULL` y `store_context_verified=False`.
Nunca se atribuye disponibilidad a una sucursal física específica.

## Gates antes de modificar el master

1. Los cuatro Excel existen y pasan la auditoría independiente de 24/24
   `COMPLETE` sobre la fecha elegida.
2. SKU, URL, precio positivo, disponibilidad online, 24 pares CP/categoría
   únicos, evidencia de CP en cabecera y fechas tienen consistencia completa.
3. El puntero activo del master apunta al mismo día que la captura. El master
   tiene pestañas Concentrado/Resumen/Control, y Fases 1 y 2 constan COMPLETE.
4. El master existente pasa la auditoría de Fase 2 antes de sustituir Soriana.
5. Los registros de otros retailers son inmutables. Se compara hash del master,
   del puntero y de cada archivo piloto antes de escribir.
6. No puede haber ejecución de Streamlit activa. El comando `--commit` solo
   acepta la fecha local actual, nunca una captura histórica.
7. Antes del commit se guardan respaldos del master y puntero. La escritura
   Excel es atómica y se vuelve a auditar Fase 2; cualquier error provoca rollback.

El puntero **no cambia**, porque no se crea otra fase ni otra fecha. La
auditoría del master cambia dinámicamente su expectativa de Soriana a 24 casos
solo si se encuentra el alcance certificado `POSTAL_ROTATION_VERIFIED` y
el contrato postal pasa todas las comprobaciones. La expectativa antigua de 6
casos sigue aplicando a masters previos.

## Probar desde PowerShell — primero preview

```powershell
cd C:\Proyectos\scraper
git fetch origin
git switch feat/soriana-postal-master-integration-20261027
git pull --ff-only

python -m pytest -q tests/test_integrate_soriana_postal.py tests/test_audit_master_phases.py tests/test_integrate_complete_retailers.py

# Este comando es read-only: inspecciona fuentes y el master activo
.\scripts\integrate_soriana_postal.ps1 -Mode Preview -CaptureDate 2026-10-27
```

La salida muestra cuántas filas de Soriana se reemplazarían, nuevo total
del master, filas de otras cadenas preservadas, y `master_modified=false`.
Si el master activo no corresponde a la fecha indicada, devolverá un error
**sin modificar nada**. No se permite incorporar las capturas del 27/10 en el
master de un día posterior.

## Commit — solo después de validar preview en el mismo día

```powershell
.\scripts\integrate_soriana_postal.ps1 -Mode Commit -CaptureDate 2026-10-27
python .\scripts\audit_master_phases.py --categories
```

El commit no se invoca desde Streamlit. Los backups estarán en
`output/checkpoints_validos/before_soriana_postal_*` y el reporte en
`output/reportes_integracion/soriana_postal_*.json`.

### No se incluye en esta fase
- Integración automática desde los botones de Streamlit.
- Escritura a Azure SQL.
- Carga de CP y fecha distintos del master activo.
- La categoría experimental Gastrointestinales.
- Reemplazar `concentrado_fase2.xlsx` o romper su runner original.

**Restricción importante:** el runner genérico de integración de Fase 2
continúa orientado a seis categorías. Para sustituir la partición postal
no ejecutar después el integrador genérico de Fase 2, porque podría
reemplazar Soriana por sus seis categorías legadas. Este flujo separado es
el contrato autorizado para la rotación postal.
