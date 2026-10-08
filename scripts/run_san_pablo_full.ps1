param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [string]$Category = "all",
    [int]$MaxPages = 20,
    [string]$ProfileDir = ".san_pablo_profile",
    [string]$DebuggerAddress = "127.0.0.1:9223",
    [switch]$UpdateConsolidated
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

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    throw "No se encontro Python valido."
}

Write-Host "============================================================"
Write-Host "Farmacias San Pablo - test completo Chrome"
Write-Host "============================================================"
Write-Host "Python   : $PythonExe"
Write-Host "Category : $Category"
Write-Host "MaxPages : $MaxPages"
Write-Host "Browser  : manual attach $DebuggerAddress"
Write-Host ("Consolidado: " + $(if ($UpdateConsolidated) { "actualizar" } else { "sin cambios" }))
Write-Host ""

$ArgsList = @(
    ".\scripts\run_san_pablo_full.py",
    "--category", $Category,
    "--max-pages", $MaxPages,
    "--debugger-address", $DebuggerAddress
)
if ($UpdateConsolidated) {
    $ArgsList += "--update-consolidated"
}

& $PythonExe @ArgsList
$code = $LASTEXITCODE

Write-Host ""
Write-Host "Salida: $ProjectDir\output\farmacias_san_pablo_test.xlsx"
if ($UpdateConsolidated) {
    Write-Host "Consolidado: $ProjectDir\output\concentrado_scraper.xlsx"
} else {
    Write-Host "Consolidado: sin cambios"
}

if ($code -ne 0) {
    Write-Warning "El test termino con observaciones (exit_code=$code). Revisa la hoja Resumen."
}

exit $code
