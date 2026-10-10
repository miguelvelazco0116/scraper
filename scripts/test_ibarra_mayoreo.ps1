param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [string]$Category = "all",
    [switch]$Headless,
    [switch]$LowMemory
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
Write-Host "Ibarra Mayoreo - test de precio y unidades por empaque"
Write-Host "============================================================"
Write-Host "Python   : $PythonExe"
Write-Host "Category : $Category"
Write-Host "Headless : $Headless"
Write-Host "LowMemory: $LowMemory"
Write-Host ""

$ArgsList = @(
    ".\scripts\test_ibarra_mayoreo.py",
    "--category", $Category
)
if ($Headless) {
    $ArgsList += "--headless"
}
if ($LowMemory) {
    $ArgsList += "--low-memory"
}

& $PythonExe @ArgsList
$code = $LASTEXITCODE

Write-Host ""
Write-Host "Salida: $ProjectDir\output\ibarra_mayoreo_test.xlsx"
Write-Host "Diagnosticos: $ProjectDir\diagnostics\ibarra_mayoreo_*"

if ($code -ne 0) {
    Write-Warning "El test termino con observaciones (exit_code=$code)."
}
