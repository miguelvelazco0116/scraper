param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [ValidateSet("cuidado-bucal", "lavanderia", "preservativos", "vias-respiratorias")]
    [string]$Category = "cuidado-bucal",
    [int]$MaxLoadMore = 100
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
Write-Host "Farmacias Guadalajara - Chrome + Selenium limpio"
Write-Host "============================================================"
Write-Host "Python    : $PythonExe"
Write-Host "Category  : $Category"
Write-Host "Load more : $MaxLoadMore"
Write-Host ""

& $PythonExe .\main.py --retailer farmacias-guadalajara --category $Category --headed --max-load-more $MaxLoadMore

if ($LASTEXITCODE -ne 0) {
    throw "Scraper termino con exit_code=$LASTEXITCODE"
}
