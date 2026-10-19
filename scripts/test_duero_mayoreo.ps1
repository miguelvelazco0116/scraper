param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [string]$Category = "all",
    [int]$MaxPages = 30
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectDir
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
Write-Host "MAYORISTA DUERO - PRUEBA AISLADA FASE 5"
Write-Host "============================================================"
Write-Host "Python: $PythonExe"
Write-Host "Categoria: $Category"
Write-Host "Paginacion maxima: $MaxPages"
& $PythonExe ".\scripts\test_duero_mayoreo.py" "--category" $Category "--max-pages" "$MaxPages"
$code = $LASTEXITCODE
Write-Host "Salida: $ProjectDir\output\duero_mayoreo_phase5_test.xlsx"
Write-Host "INTEGRACION AL MASTER: OMITIDA (validacion live pendiente)"
exit $code
