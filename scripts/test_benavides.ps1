param(
    [string]$ProjectDir = "",
    [string]$PythonExe = "",
    [ValidateSet("all", "medicamentos-respiratorios", "bienestar-sexual", "higiene-bucal")]
    [string]$Category = "all",
    [int]$MaxPages = 60,
    [switch]$Headed
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

if ($Category -eq "all") {
    Write-Host ""
    Write-Host "Evaluando retailers completos de Fase 4; parciales quedan pendientes..."
    & ".\scripts\integrate_complete_retailers.ps1" `
        -Phase 4 `
        -ProjectDir $ProjectDir `
        -PythonExe $PythonExe

    $integrationCode = $LASTEXITCODE
    if ($integrationCode -ne 0) {
        Write-Warning "No se pudo actualizar el master de Fase 4."
        if ($code -eq 0) { $code = $integrationCode }
    }
}

exit $code
