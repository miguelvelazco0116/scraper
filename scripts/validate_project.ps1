param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [switch]$IncludeBrowser
)

$ErrorActionPreference = "Continue"
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
    $PythonExe = "python"
}

$Results = @()

Write-Host "=============================================================================="
Write-Host "VALIDACION INTEGRAL DEL PROYECTO"
Write-Host "=============================================================================="
Write-Host "Python      : $PythonExe"
Write-Host "Browser tests: $IncludeBrowser"
Write-Host "Consolidado : NO se modifica"
Write-Host ""

Write-Host "[1/3] Pytest"
& $PythonExe -m pytest -q
$PytestCode = $LASTEXITCODE
$Results += [PSCustomObject]@{ Check = "pytest"; ExitCode = $PytestCode }
Write-Host ""

Write-Host "[2/3] Project healthcheck"
& $PythonExe .\scripts\project_healthcheck.py
$HealthCode = $LASTEXITCODE
$Results += [PSCustomObject]@{ Check = "healthcheck"; ExitCode = $HealthCode }
Write-Host ""

Write-Host "[3/3] Scrapy regression"
& .\scripts\validate_scrapy_candidates.ps1 -Group regression -PythonExe $PythonExe
$RegressionCode = $LASTEXITCODE
$Results += [PSCustomObject]@{ Check = "scrapy_regression"; ExitCode = $RegressionCode }
Write-Host ""

if ($IncludeBrowser) {
    Write-Host "[extra] Browser-required retailers"
    & .\scripts\validate_browser_required.ps1 -ProjectDir $ProjectDir -PythonExe $PythonExe
    $BrowserCode = $LASTEXITCODE
    $Results += [PSCustomObject]@{ Check = "browser_required"; ExitCode = $BrowserCode }
    Write-Host ""
}

Write-Host "=============================================================================="
Write-Host "RESUMEN VALIDACION"
Write-Host "=============================================================================="
$Results | Format-Table -AutoSize

$Failed = @($Results | Where-Object { $_.ExitCode -ne 0 })
if ($Failed.Count -gt 0) {
    Write-Warning "La validacion termino con observaciones."
    exit 1
}

Write-Host "VALIDACION COMPLETA: OK"
exit 0
