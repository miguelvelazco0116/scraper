param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [string]$CdpUrl = "",
    [ValidateSet("all", "medicamentos-respiratorio", "higiene-bucal")]
    [string]$Category = "all",
    [switch]$Probe,
    [ValidateRange(1, 200)]
    [int]$MaxPages = 100
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
if (-not $PythonExe -or -not (Test-Path $PythonExe)) {
    throw "Entorno virtual Python no encontrado."
}


Write-Host "FARMACIAS YZA - TESTING CLI"
Write-Host "CP fijo     : 11590"
Write-Host "Categorias  : $Category"
Write-Host "Master      : NO SE MODIFICA"
Write-Host "Python      : $PythonExe"
$argsList = @("--category", $Category)
if ($CdpUrl) { $argsList += @("--cdp-url", $CdpUrl) }
Write-Host ("Navegador  : " + $(if ($CdpUrl) { "Chrome manual (CDP)" } else { "Chrome automatico" }))
if ($Probe) {
    if ($Category -eq "all") { $Category = "higiene-bucal" }
    & $PythonExe ".\scripts\probe_yza.py" @argsList
} else {
    & $PythonExe ".\scripts\test_yza.py" @argsList --max-pages $MaxPages
}
exit $LASTEXITCODE
