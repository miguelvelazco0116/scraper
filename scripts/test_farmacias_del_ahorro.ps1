param(
    [string]$Category = "anti-acne",
    [string]$ProjectDir = "",
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($ProjectDir)) {
    $ScriptFile = $MyInvocation.MyCommand.Path
    if ([string]::IsNullOrWhiteSpace($ScriptFile)) { throw "No se pudo determinar la ruta del proyecto. Especifica -ProjectDir." }
    $ProjectDir = Split-Path -Parent (Split-Path -Parent $ScriptFile)
}
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
