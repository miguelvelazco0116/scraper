param(
    [string]$Category = "cuidado-bucal",
    [switch]$UpdateConsolidated
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = "C:\Proyectos\venvs\scraper\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

$argsList = @(
    ".\scripts\scrape_farmacias_guadalajara_manual_session.py",
    "--category", $Category
)

if ($UpdateConsolidated) {
    $argsList += "--update-consolidated"
}

& $Python @argsList
exit $LASTEXITCODE
