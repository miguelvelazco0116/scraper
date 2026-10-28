param(
    [Parameter(Mandatory=$true)]
    [ValidateSet(1,3,4,5)][int]$Phase,
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
    throw "Entorno Python scraper no encontrado."
}

Write-Host "FASE $Phase - INTEGRAR CAPTURAS EXISTENTES"
Write-Host "Sin navegadores, sin capturas repetidas."
Write-Host "Sólo retailers COMPLETE de hoy; integrador incremental auditado."
Write-Host "Requiere fases previas COMPLETE; no modifica Soriana con fuentes legadas."
if ($Phase -eq 5) {
    Write-Host "Duero: precios publicos del catalogo, sin comprobacion de ubicacion o CP."
}
$arguments = @(".\scripts\integrate_existing_phases.py", "--phase", "$Phase")
if ($Preview) {
    Write-Host "MODO: Preview (sin escritura master)"
    $arguments += "--preview"
} else {
    Write-Host "MODO: Commit (respaldo, validación posterior, rollback)"
    $arguments += "--commit"
}
& $PythonExe @arguments
exit $LASTEXITCODE
