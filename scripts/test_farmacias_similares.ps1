param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [string]$ProfileDir = ".similares_profile"
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
Write-Host "Farmacias Similares - test categorias activas"
Write-Host "============================================================"
Write-Host "Python: $PythonExe"
Write-Host ""

& $PythonExe .\scripts\test_farmacias_similares.py
$code = $LASTEXITCODE

Write-Host ""
Write-Host "Salida: $ProjectDir\output\farmacias_similares_test.xlsx"
Write-Host "Diagnosticos: $ProjectDir\diagnostics\farmacias_similares_*"

if ($code -ne 0) {
    Write-Warning "El test termino con observaciones (exit_code=$code)."
}
