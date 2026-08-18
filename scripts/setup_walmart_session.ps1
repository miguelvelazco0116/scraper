param(
    [string]$ProfileDir = "C:\scraper_profiles\walmart_sc_toreo"
)

$ErrorActionPreference = "Stop"

Write-Host "Preparando perfil persistente de Walmart en: $ProfileDir"
New-Item -ItemType Directory -Force -Path $ProfileDir | Out-Null

python -m pip install -r requirements.txt
playwright install chromium

python scripts\walmart_prepare_session.py --profile-dir "$ProfileDir" --store-id 2344

Write-Host ""
Write-Host "Perfil preparado. No lo muevas dentro del repositorio y no lo subas a GitHub."
Write-Host "Ruta recomendada para la variable WALMART_PROFILE_DIR:"
Write-Host $ProfileDir
Write-Host ""
Write-Host "IMPORTANTE: para las corridas de Walmart, inicia el runner self-hosted de GitHub de forma interactiva"
Write-Host "en esta misma sesion de Windows. No lo ejecutes como servicio si necesitas navegador visible."
