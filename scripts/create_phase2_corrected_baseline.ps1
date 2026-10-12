param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
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
Write-Host "CREAR BASELINE CORREGIDA - FASE 2"
Write-Host "========================================================================"
Write-Host "Fuente : $ProjectDir\output\concentrado_fase2.xlsx"
Write-Host "Destino: $ProjectDir\output\fase2_muestra_inicial_valida_v2_2026-10-12.xlsx"
Write-Host ""
Write-Host "La baseline original se conserva sin cambios."
Write-Host ""

& $PythonExe ".\scripts\create_phase2_corrected_baseline.py"
exit $LASTEXITCODE
