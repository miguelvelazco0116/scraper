param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [string]$ProfileDir = ".chedraui_profile",
    [switch]$Headless
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectDir

if ([string]::IsNullOrWhiteSpace($PythonExe) -and $env:VIRTUAL_ENV) {
    $candidate = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
    if (Test-Path $candidate) {
        $PythonExe = $candidate
    }
}

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $candidate = "C:\Proyectos\venvs\scraper\Scripts\python.exe"
    if (Test-Path $candidate) {
        $PythonExe = $candidate
    }
}

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        $PythonExe = $cmd.Source
    }
}

if ([string]::IsNullOrWhiteSpace($PythonExe) -or -not (Test-Path $PythonExe)) {
    throw "No se encontro Python valido para ejecutar la Fase 1."
}

Write-Host "========================================================================"
Write-Host "SCRAPER PRODUCTIVO - FASE 1"
Write-Host "========================================================================"
Write-Host "Retailers : Farmacias del Ahorro + Ibarra Mayoreo + Chedraui"
Write-Host "Cobertura : TODAS las categorias activas"
Write-Host "Python    : $PythonExe"
Write-Host "Profile   : $ProfileDir"
Write-Host "Salida    : $ProjectDir\output\concentrado_productivo.xlsx"
Write-Host ""
Write-Host "El master solo se reemplaza si TODAS las categorias pasan calidad."
Write-Host ""

$ArgsList = @(
    ".\scripts\run_phase1_production.py",
    "--profile-dir", $ProfileDir
)

if (-not $Headless) {
    $ArgsList += "--headed"
}

& $PythonExe @ArgsList
$code = $LASTEXITCODE

Write-Host ""
if ($code -eq 0) {
    Write-Host "========================================================================"
    Write-Host "FASE 1 PRODUCTIVA PUBLICADA"
    Write-Host "========================================================================"
    Write-Host "Archivo: $ProjectDir\output\concentrado_productivo.xlsx"
} else {
    Write-Warning "La Fase 1 NO fue publicada. El master productivo anterior se conserva."
    Write-Host "Diagnostico: $ProjectDir\diagnostics\phase1_production\last_run.json"
}

exit $code
