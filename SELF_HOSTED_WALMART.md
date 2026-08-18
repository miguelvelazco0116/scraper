# Walmart con runner self-hosted

## Requisito de seguridad

GitHub recomienda usar self-hosted runners con repositorios privados. Este repositorio es público al momento de preparar esta solución, por lo que **no se debe registrar una computadora personal como self-hosted runner de este repositorio mientras siga público**.

Opciones seguras:

1. Cambiar `scraper` a privado y registrar el runner ahí.
2. Mantener `scraper` público, pero crear un repositorio privado separado para el workflow de ejecución y registrar el runner únicamente en ese repositorio privado.

El workflow `.github/workflows/full-self-hosted.yml` está limitado a `workflow_dispatch`, pero eso no sustituye la recomendación de aislar el runner en un repositorio privado.

## Preparación de Walmart

En la computadora Windows que funcionará como runner:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_walmart_session.ps1
```

Ruta de perfil predeterminada:

```text
C:\scraper_profiles\walmart_sc_toreo
```

Completa manualmente cualquier verificación de Walmart y confirma SC Toreo / CP 11220. La verificación no se automatiza.

## Runner interactivo

Para las corridas de Walmart, ejecuta el runner en una sesión de Windows iniciada y de forma interactiva, porque Walmart se abre con navegador visible (`--headed`).

El workflow espera las etiquetas estándar:

```text
self-hosted
windows
x64
```

## Output

La ejecución unificada corre todas las categorías habilitadas de Soriana y Walmart y genera:

```text
output/concentrado_scraper.xlsx
```

con hojas:

- `Concentrado`
- `Resumen`

Si Walmart vuelve a solicitar verificación, renueva manualmente la sesión con `setup_walmart_session.ps1`.
