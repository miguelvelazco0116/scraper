param(
    [string]$Category = "all",
    [int]$RetryFailedAtEnd = 1,
    [switch]$Headless,
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

Write-Host "============================================================"
Write-Host "FASE 5 - HEB"
Write-Host "============================================================"
Write-Host "Python     : $PythonExe"
Write-Host "Categoria  : $Category"
Write-Host "Reintentos : $RetryFailedAtEnd"
if ($Headless) {
    Write-Host "Chrome     : HEADLESS"
} else {
    Write-Host "Chrome     : VISIBLE"
}
Write-Host ""

$ArgsList = @(
    ".\scripts\test_heb.py",
    "--category", $Category,
    "--retry-failed-at-end", "$RetryFailedAtEnd"
)

if ($Headless) {
    $ArgsList += "--headless"
}

& $PythonExe @ArgsList
$code = $LASTEXITCODE

Write-Host ""
Write-Host "Salida: $ProjectDir\output\heb_phase5_test.xlsx"
Write-Host "Diagnosticos: $ProjectDir\diagnostics\heb\"

if ($code -ne 0) {
    Write-Warning "El test HEB termino con observaciones (exit_code=$code)."
}

exit $code
