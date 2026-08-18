# Walmart sin VM: sesión portable + GitHub Actions

Esta es la ruta recomendada cuando no se quiere usar una VM ni una PC como self-hosted runner.

## Arquitectura

```text
PC del usuario (sólo para verificar Walmart una vez)
        |
        | exporta sesión Playwright
        v
GitHub Secret: WALMART_SESSION_GZIP_B64
        |
        v
GitHub-hosted Actions (ubuntu-latest)
        |
        +--> Soriana / todas las categorías
        +--> Walmart / SC Toreo / todas las categorías
        |
        v
output/concentrado_scraper.xlsx
```

## 1. Preparar dependencias localmente

```bash
pip install -r requirements.txt
playwright install chromium
```

## 2. Exportar la sesión de Walmart

```bash
python scripts/export_walmart_session.py
```

Se abrirá Chromium. Completa manualmente cualquier verificación que Walmart solicite y confirma:

```text
SC Toreo
Store ID 2344
CP 11220
```

Después vuelve a la terminal y presiona ENTER.

Se crean dos archivos locales:

```text
walmart_session.json
walmart_session.secret.txt
```

No los subas a Git. `.gitignore` ya los excluye.

## 3. Guardar el Secret

En GitHub:

`Repository -> Settings -> Secrets and variables -> Actions -> New repository secret`

Nombre:

```text
WALMART_SESSION_GZIP_B64
```

Valor: copia todo el contenido de:

```text
walmart_session.secret.txt
```

## 4. Ejecutar

En GitHub Actions selecciona:

```text
Full Scraper - Hosted Session
```

Pulsa `Run workflow`.

El workflow reconstruye la sesión en `$RUNNER_TEMP`, ejecuta las 7 combinaciones actualmente configuradas y elimina el archivo temporal al finalizar.

## 5. Resultado esperado

Un único artifact con:

```text
output/concentrado_scraper.xlsx
```

Hojas:

- `Concentrado`
- `Resumen`

## Renovación de sesión

Si Walmart devuelve `BLOCKED`, no se intenta resolver automáticamente. Repite:

```bash
python scripts/export_walmart_session.py
```

y reemplaza el valor del Secret `WALMART_SESSION_GZIP_B64`.

## Seguridad

El archivo exportado contiene cookies y estado de navegador. Trátalo como una credencial. No lo publiques, no lo adjuntes en issues/PRs y no lo guardes en el repositorio.
