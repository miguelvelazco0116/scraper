param(
    [int]$SorianaDelaySeconds = 60,
    [int]$RetryDelaySeconds = 120
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = "C:\Proyectos\venvs\scraper\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

Write-Host "============================================================"
Write-Host "SORIANA BLOQUEADOS + DISPONIBILIDAD SORIANA / CHEDRAUI"
Write-Host "============================================================"
Write-Host "Python        : $Python"
Write-Host "Soriana delay : $SorianaDelaySeconds s"
Write-Host "Retry delay   : $RetryDelaySeconds s"
Write-Host ""

& $Python ".\scripts\test_soriana_chedraui_availability.py" --soriana-delay-seconds $SorianaDelaySeconds --retry-delay-seconds $RetryDelaySeconds

exit $LASTEXITCODE
