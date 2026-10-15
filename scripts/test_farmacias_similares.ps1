param(
    [string]$Category = "all",
    [int]$RetryFailedAtEnd = 1,
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
Write-Host "FASE 5 - Farmacias Similares"
Write-Host "============================================================"
Write-Host "Python     : $PythonExe"
Write-Host "Categoria  : $Category"
Write-Host "Reintentos : $RetryFailedAtEnd"
Write-Host ""

& $PythonExe .\scripts\test_farmacias_similares.py `
    --category $Category `
    --retry-failed-at-end $RetryFailedAtEnd
$code = $LASTEXITCODE

Write-Host ""
Write-Host "Salida: $ProjectDir\output\farmacias_similares_test.xlsx"
Write-Host "Diagnosticos: $ProjectDir\diagnostics\farmacias_similares_*"

if ($code -ne 0) {
    Write-Warning "El test termino con observaciones (exit_code=$code)."
}
