param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [string]$ProfileDir = ".walmart_profile",
    [string]$StorageState = "",
    [switch]$Headless
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

$ArgsList = @(
    ".\scripts\test_walmart_phase3.py",
    "--profile-dir", $ProfileDir
)
if (-not [string]::IsNullOrWhiteSpace($StorageState)) {
    $ArgsList += @("--storage-state", $StorageState)
}
if ($Headless) {
    $ArgsList += "--headless"
}

Write-Host "========================================================================"
Write-Host "WALMART - TEST FASE 3"
Write-Host "========================================================================"
Write-Host "Python : $PythonExe"
Write-Host "Perfil : $ProfileDir"
Write-Host "Modo   : $(if ($Headless) { 'HEADLESS' } else { 'VISIBLE' })"
Write-Host ""

& $PythonExe @ArgsList
$code = $LASTEXITCODE

Write-Host ""
Write-Host "Salida: $ProjectDir\output\walmart_phase3_test.xlsx"
if ($code -ne 0) {
    Write-Warning "Walmart Fase 3 termino con observaciones (exit_code=$code)."
}
exit $code
