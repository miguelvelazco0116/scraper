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
    $candidate = Join-Path $ProjectDir ".venv\Scripts\python.exe"
    if (Test-Path $candidate) { $PythonExe = $candidate }
}

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd) { $PythonExe = $cmd.Source }
}

if ([string]::IsNullOrWhiteSpace($PythonExe) -or -not (Test-Path $PythonExe)) {
    throw "No se encontro Python valido."
}

Write-Host "============================================================"
Write-Host "SCRAPER COMPLETO - RETAILERS ACTIVOS"
Write-Host "============================================================"
Write-Host "Activos : Soriana, Chedraui, Farmacias del Ahorro, Farmacias San Pablo, Ibarra Mayoreo, Bodega Aurrera"
Write-Host "Pausa   : Walmart, Farmacias Guadalajara"
Write-Host "Aviso   : Bodega Aurrera puede solicitar verificacion manual en Chrome."
Write-Host "          Si aparece, completala y deja la ventana abierta para continuar."
Write-Host "Python  : $PythonExe"
Write-Host ""

& $PythonExe .\scripts\run_all_retailers.py --local-browser
$code = $LASTEXITCODE

Write-Host ""
Write-Host "Salida final: $ProjectDir\output\concentrado_scraper.xlsx"
Write-Host "Logs: $ProjectDir\diagnostics\run_all"

if ($code -ne 0) {
    Write-Warning "La corrida termino con observaciones (exit_code=$code). Revisa el RESUMEN FINAL y diagnostics\run_all."
}
