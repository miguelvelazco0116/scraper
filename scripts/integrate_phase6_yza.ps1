param(
    [string]$ProjectDir = "",
    [string]$PythonExe = "",
    [switch]$Preview
)
$ErrorActionPreference = "Stop"
# Resolve the script directory only AFTER PowerShell has bound parameters.
# $PSScriptRoot can be empty inside default parameter expressions.
if ([string]::IsNullOrWhiteSpace($ProjectDir)) {
    $ScriptFile = $MyInvocation.MyCommand.Path
    if ([string]::IsNullOrWhiteSpace($ScriptFile)) {
        throw "No se pudo determinar la ruta del proyecto. Especifica -ProjectDir."
    }
    $ScriptFolder = Split-Path -Parent $ScriptFile
    $ProjectDir = Split-Path -Parent $ScriptFolder
}
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
