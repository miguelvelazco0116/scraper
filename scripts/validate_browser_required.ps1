param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Continue"
Set-Location $ProjectDir

Write-Host "=============================================================================="
Write-Host "VALIDACION RETAILERS CON NAVEGADOR / SESION"
Write-Host "=============================================================================="
Write-Host "Consolidado: NO se modifica"
Write-Host ""

Write-Host "[1/2] Farmacias Similares"
& .\scripts\test_farmacias_similares.ps1 -ProjectDir $ProjectDir -PythonExe $PythonExe -ProfileDir ".similares_profile"
$SimilaresCode = $LASTEXITCODE
Write-Host "  -> exit=$SimilaresCode"
Write-Host ""

Write-Host "[2/2] Farmacias San Pablo"
& .\scripts\run_san_pablo_full.ps1 -ProjectDir $ProjectDir -PythonExe $PythonExe -ProfileDir ".san_pablo_profile"
$SanPabloCode = $LASTEXITCODE
Write-Host "  -> exit=$SanPabloCode"
Write-Host ""

Write-Host "=============================================================================="
Write-Host "RESUMEN"
Write-Host "=============================================================================="
Write-Host "Farmacias Similares : exit=$SimilaresCode"
Write-Host "Farmacias San Pablo : exit=$SanPabloCode"
Write-Host ""
Write-Host "Outputs:"
Write-Host "  $ProjectDir\output\farmacias_similares_test.xlsx"
Write-Host "  $ProjectDir\output\farmacias_san_pablo_test.xlsx"
Write-Host ""
Write-Host "Perfiles persistentes:"
Write-Host "  $ProjectDir\.similares_profile"
Write-Host "  $ProjectDir\.san_pablo_profile"

if ($SimilaresCode -eq 0 -and $SanPabloCode -eq 0) {
    exit 0
}

exit 1
