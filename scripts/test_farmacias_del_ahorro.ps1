param(
    [string]$Category = "anti-acne",
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
    throw "No se encontró Python válido."
}

Write-Host "========================================================================"
Write-Host "FARMACIAS DEL AHORRO - TEST AISLADO"
Write-Host "========================================================================"
Write-Host "Category : $Category"
Write-Host "Python   : $PythonExe"
Write-Host ""

& $PythonExe ".\scripts\test_farmacias_del_ahorro.py" --category $Category
$code = $LASTEXITCODE

Write-Host ""
Write-Host "Salida: $ProjectDir\output\farmacias_del_ahorro_test.xlsx"

if ($code -ne 0) {
    Write-Warning "El test terminó con observaciones (exit_code=$code)."
}

exit $code
