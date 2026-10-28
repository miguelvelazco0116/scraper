param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [switch]$Preview
)
$ErrorActionPreference = "Stop"
Set-Location $ProjectDir
if (-not $PythonExe -and $env:VIRTUAL_ENV) {
    $candidate = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
    if (Test-Path $candidate) { $PythonExe = $candidate }
}
if (-not $PythonExe) {
    $candidate = "C:\Proyectos\venvs\scraper\Scripts\python.exe"
    if (Test-Path $candidate) { $PythonExe = $candidate }
}
if (-not $PythonExe -or -not (Test-Path $PythonExe)) {
    throw "Activa el entorno scraper para integrar YZA."
}
Write-Host "FASE 6 - FARMACIAS YZA - INTEGRAR CAPTURA EXISTENTE"
Write-Host "SIN SCRAPING; sin localidad ni CP verificado."
$argsList = @(".\scripts\integrate_phase6_yza.py")
if ($Preview) {
    $argsList += "--preview"
    Write-Host "PREVIEW: master SIN CAMBIOS."
} else {
    $argsList += "--commit"
    Write-Host "COMMIT: validacion, respaldo y auditoria."
}
& $PythonExe @argsList
exit $LASTEXITCODE
