param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [ValidateSet("all", "medicamentos-respiratorios", "bienestar-sexual", "higiene-bucal")]
    [string]$Category = "all",
    [int]$MaxPages = 60,
    [switch]$Headed
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

Write-Host "============================================================================"
Write-Host "FARMACIAS BENAVIDES - TEST AISLADO"
Write-Host "============================================================================"
Write-Host "Python     : $PythonExe"
Write-Host "Category   : $Category"
Write-Host "MaxPages   : $MaxPages"
Write-Host "Navegador  : $(if ($Headed) { 'VISIBLE' } else { 'HEADLESS' })"
Write-Host "Salida     : $ProjectDir\output\benavides_test.xlsx"
Write-Host ""

$ArgsList = @(
    ".\scripts\test_benavides.py",
    "--category", $Category,
    "--max-pages", "$MaxPages"
)

if ($Headed) {
    $ArgsList += "--headed"
}

& $PythonExe @ArgsList
$code = $LASTEXITCODE

Write-Host ""
Write-Host "Salida: $ProjectDir\output\benavides_test.xlsx"
Write-Host "Diagnosticos: $ProjectDir\diagnostics\benavides_*"

if ($code -ne 0) {
    Write-Warning "El test termino con observaciones (exit_code=$code)."
}

exit $code
