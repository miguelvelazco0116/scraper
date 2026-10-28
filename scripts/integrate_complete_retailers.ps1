param(
    [Parameter(Mandatory = $true)]
    [ValidateSet(1, 2, 3, 4, 5)]
    [int]$Phase,
    [switch]$Preview,
    [switch]$ApproveDueroOnline,
    [string]$ProjectDir = "",
    [string]$PythonExe = ""
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

if ([string]::IsNullOrWhiteSpace($PythonExe) -and $env:VIRTUAL_ENV) {
    $candidate = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
    if (Test-Path $candidate) { $PythonExe = $candidate }
}
if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $candidate = "C:\Proyectos\venvs\scraper\Scripts\python.exe"
    if (Test-Path $candidate) { $PythonExe = $candidate }
}
if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd) { $PythonExe = $cmd.Source }
}
if ([string]::IsNullOrWhiteSpace($PythonExe) -or -not (Test-Path $PythonExe)) {
    throw "No se encontro Python valido."
}

Write-Host "========================================================================"
Write-Host "INTEGRACION INCREMENTAL - SOLO RETAILERS COMPLETOS"
Write-Host "========================================================================"
Write-Host "Fase    : $Phase"
Write-Host "Modo    : $(if ($Preview) { 'PREVIEW (SIN ESCRITURA)' } else { 'INTEGRAR' })"
Write-Host "Python  : $PythonExe"

$ArgsList = @(".\scripts\integrate_complete_retailers.py", "--phase", "$Phase")
if ($Preview) { $ArgsList += "--preview" }
if ($ApproveDueroOnline) {
    if ($Phase -ne 5) {
        throw "-ApproveDueroOnline solo se permite con -Phase 5."
    }
    $ArgsList += "--approve-duero-online"
    Write-Host "Duero: solo precios publicos; parametro legado aceptado sin comprobar ubicacion ni CP"
}

& $PythonExe @ArgsList
exit $LASTEXITCODE
