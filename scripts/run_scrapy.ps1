param(
    [Parameter(Mandatory=$true)]
    [string]$Retailer,

    [Parameter(Mandatory=$true)]
    [string]$Category,

    [string]$Location,

    [int]$MaxPages = 100,

    [int]$RowsPerPage = 50,

    [switch]$UpdateConsolidated
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = "C:\Proyectos\venvs\scraper\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

$ArgsList = @(
    ".\scripts\run_scrapy.py",
    "--retailer", $Retailer,
    "--category", $Category,
    "--max-pages", $MaxPages,
    "--rows-per-page", $RowsPerPage
)

if ($Location) {
    $ArgsList += @("--location", $Location)
}

if ($UpdateConsolidated) {
    $ArgsList += "--update-consolidated"
}

& $Python @ArgsList
exit $LASTEXITCODE
