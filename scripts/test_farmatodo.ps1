param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [ValidateSet("all", "respiratorios", "cuidado-bucal", "higiene-personal")]
    [string]$Category = "all",
    [string]$CdpUrl = "",
    [ValidateRange(1, 200)][int]$MaxRounds = 100,
    [switch]$Probe,
    [switch]$Headed
)
$ErrorActionPreference = "Stop"
Set-Location $ProjectDir

if (-not $PythonExe -and $env:VIRTUAL_ENV) {
    $candidate = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
    if (Test-Path $candidate) { $PythonExe = $candidate }
}
if (-not $PythonExe) {
    $candidate = "C:\Proyectos\venvs\scraper\Scripts\python.exe"
    if (Test-Path $candidate) { $PythonExe = $candidate }
}
if (-not $PythonExe -or -not (Test-Path $PythonExe)) {
    throw "Activa primero el entorno virtual del scraper."
}

$argsList = @(
    ".\scripts\test_farmatodo.py",
    "--category", $Category,
    "--max-rounds", "$MaxRounds"
)
if ($CdpUrl) { $argsList += @("--cdp-url", $CdpUrl) }
if ($Probe) { $argsList += "--probe" }
if ($Headed) { $argsList += "--headed" }
Write-Host "FARMATODO | PILOTO CLI | SIN COMPROBACION DE CP"
Write-Host "Categoria: $Category | Probe: $Probe"
Write-Host "Salida: output\pilots\farmatodo\catalogo_online.xlsx"
Write-Host "Master productivo: SIN CAMBIOS"
& $PythonExe @argsList
exit $LASTEXITCODE
