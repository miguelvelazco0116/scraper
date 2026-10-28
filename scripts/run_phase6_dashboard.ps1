param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [switch]$SkipIntegration
)
$ErrorActionPreference = "Stop"
Set-Location $ProjectDir

Write-Host "FASE 6 - FARMACIAS YZA (2 CATEGORIAS, CATALOGO ONLINE)"
Write-Host "Sin certificacion de tienda, CP, precio o stock por localidad."
Write-Host "Solo se hace una captura; se mantienen los 5 primeros phases."

# Existing YZA pilot script owns Chrome CDP and same-day source validation.
& (Join-Path $PSScriptRoot "run_phase6_pilot.ps1") `
    -Mode capture -Retailer farmacias-yza -ProjectDir $ProjectDir -PythonExe $PythonExe
$captureCode = $LASTEXITCODE
if ($captureCode -ne 0) {
    Write-Warning "Captura YZA incompleta. No se publica la Fase 6."
    exit $captureCode
}
if ($SkipIntegration) {
    Write-Host "SOLO CAPTURA YZA: master sin cambios."
    exit 0
}
Write-Host "CAPTURA COMPLETE_PILOT; INTEGRACION VERIFICADA"
& (Join-Path $PSScriptRoot "integrate_phase6_yza.ps1") `
    -ProjectDir $ProjectDir -PythonExe $PythonExe
$integrationCode = $LASTEXITCODE
if ($integrationCode -ne 0) {
    Write-Warning "La captura YZA se conserva; la integracion quedo bloqueada."
    exit $integrationCode
}
Write-Host "FASE 6 FINALIZADA. No se reejecuto el scraper."
exit 0
