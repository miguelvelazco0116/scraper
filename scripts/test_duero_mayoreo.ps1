param(
    [string]$ProjectDir = "",
    [string]$PythonExe = "",
    [string]$Category = "all",
    [ValidateRange(2, 100)]
    [int]$MaxPages = 30,
    [ValidateRange(0, 15)]
    [double]$DelaySeconds = 0.7
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($ProjectDir)) {
    $ScriptFile = $MyInvocation.MyCommand.Path
    if ([string]::IsNullOrWhiteSpace($ScriptFile)) { throw "No se pudo determinar la ruta del proyecto. Especifica -ProjectDir." }
    $ProjectDir = Split-Path -Parent (Split-Path -Parent $ScriptFile)
}
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
Write-Host "Paginacion maxima por recorrido: $MaxPages"
Write-Host "Auditoria : dos recorridos API independientes, limit=50 y limit=250"
Write-Host "Gate      : requiere confirmar página terminal vacía y variantes idénticas"
Write-Host "Pausa API : $DelaySeconds segundos"
& $PythonExe ".\scripts\test_duero_mayoreo.py" "--category" $Category "--max-pages" "$MaxPages" "--delay-seconds" "$DelaySeconds"
$code = $LASTEXITCODE
Write-Host "Salida: $ProjectDir\output\duero_mayoreo_phase5_test.xlsx"
Write-Host "INTEGRACION AL MASTER: OMITIDA (validacion live pendiente)"
exit $code
