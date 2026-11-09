# Mercado Libre México — integración aislada (fase inicial)

## Alcance

Se reutilizaron del proyecto de PabloAlaniz/MercadoLibre-Scraper los conceptos de:
separación entre captura y normalización, entidad de publicación, tests unitarios
y exportación desacoplada. **No se copió la interfaz Dash/Flask/SocketIO, los
scrapers de automóviles o inmuebles ni el parser de precios ARS/USD.**

Fuente: https://github.com/PabloAlaniz/MercadoLibre-Scraper (README y código, licencia MIT declarada).

## Decisión de acceso

El código agregado únicamente **normaliza respuestas suministradas desde una API
autorizada**. No implementa extracción HTML, evasión de bloqueos ni búsqueda
automática de productos. Antes de habilitar red, confirmar permisos de acceso,
endpoints disponibles y uso permitido de los datos según los Términos del
Programa de Desarrolladores en México:
https://developers.mercadolibre.com.mx/es-mx-terminos-y-condiciones

## Contrato de calidad

- ID de publicación MLM estable, URL HTTPS de mercadolibre.com.mx,
  título y precio positivo MXN obligatorios.
- Deduplicación por ID de publicación; rechazos explicados por fila.
- El precio regular y la promoción quedan vacíos sin evidencia confiable.
- No equiparar disponibilidad de publicación con inventario por código postal.
- Captura aislada: ninguna fila entra al master productivo automáticamente.
- Estado COMPLETE solo tras acceso permitido, auditoría de páginas,
  cobertura, duplicados, categorías y precios, sin errores no resueltos.
- Los tres intentos por categoría se implementarán en el runner operativo;
  nunca usar reintentos para superar un bloqueo explícito.

## Siguiente paso antes de ejecutar

Confirmar el acceso API aprobado para el caso de uso; integrar una capa de
consulta y paginación sobre endpoints/documentación vigentes y autorizados.
Agregar categorías YAML, runner CLI, checkpoint, informe de cobertura y
prueba en vivo aislada. No habilitar producción sin validación.

## Pruebas locales

```bash
python -m pytest -q tests/test_mercado_libre.py
```
