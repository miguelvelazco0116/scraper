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
Write-Host "Bodega Aurrera - test cuidado bucal"
Write-Host "============================================================"
Write-Host "Python: $PythonExe"
Write-Host ""
Write-Host "IMPORTANTE: si aparece una verificacion de identidad en Chrome,"
Write-Host "completala manualmente. El scraper no intenta evadirla."
Write-Host ""

& $PythonExe .\scripts\test_bodega_aurrera.py
$code = $LASTEXITCODE

Write-Host ""
Write-Host "Salida: $ProjectDir\output\bodega_aurrera_cuidado_bucal_test.xlsx"
Write-Host "Diagnosticos: $ProjectDir\diagnostics\bodega_aurrera_*"

if ($code -ne 0) {
    Write-Warning "El test termino con observaciones (exit_code=$code)."
}
