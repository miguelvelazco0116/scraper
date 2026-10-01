param(
    [string]$Category = "detergentes-suavizantes",
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
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd) { $PythonExe = $cmd.Source }
}

if ([string]::IsNullOrWhiteSpace($PythonExe) -or -not (Test-Path $PythonExe)) {
    throw "No se encontro Python valido."
}

Write-Host "============================================================"
Write-Host "La Comer - test"
Write-Host "============================================================"
Write-Host "Categoria: $Category"
Write-Host "Python   : $PythonExe"
Write-Host ""

& $PythonExe .\scripts\test_la_comer.py --category $Category
$code = $LASTEXITCODE

Write-Host ""
Write-Host "Salida: $ProjectDir\output\la_comer_$($Category)_test.xlsx"
Write-Host "Diagnosticos: $ProjectDir\diagnostics\la_comer_*"

if ($code -ne 0) {
    Write-Warning "El test termino con observaciones (exit_code=$code)."
}
