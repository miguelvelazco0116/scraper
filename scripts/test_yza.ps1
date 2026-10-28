param(
    [string]$ProjectDir = "",
    [string]$PythonExe = "",
    [string]$CdpUrl = "",
    [ValidateSet("all", "medicamentos-respiratorio", "higiene-bucal")]
    [string]$Category = "all",
    [switch]$Probe,
    [ValidateRange(1, 200)]
    [int]$MaxPages = 100
)

$ErrorActionPreference = "Stop"
# PowerShell script variables are not guaranteed during param default evaluation.
if ([string]::IsNullOrWhiteSpace($ProjectDir)) {
    $ScriptFile = $MyInvocation.MyCommand.Path
    if ([string]::IsNullOrWhiteSpace($ScriptFile)) {
        throw "No se pudo determinar la ruta del proyecto. Especifica -ProjectDir."
    }
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
if (-not $PythonExe -or -not (Test-Path $PythonExe)) {
    throw "Entorno virtual Python no encontrado."
}


if (-not $CdpUrl) {
    throw "Este piloto de YZA utiliza Chrome existente. Usa run_phase6_pilot.ps1 -Retailer farmacias-yza para detectar CDP sin CP obligatorio, o indica -CdpUrl."
}
Write-Host "FARMACIAS YZA - TESTING CLI"
Write-Host "Localidad   : NO VERIFICADA; CP NO OBLIGATORIO"
Write-Host "Categorias  : $Category"
Write-Host "Master      : NO SE MODIFICA"
Write-Host "Python      : $PythonExe"
if ($Probe -and $Category -eq "all") { $Category = "higiene-bucal" }
$argsList = @("--category", $Category)
if ($CdpUrl) { $argsList += @("--cdp-url", $CdpUrl) }
Write-Host "Navegador  : Chrome del operador por CDP, no se modifica localidad"
if ($Probe) {
    & $PythonExe ".\scripts\probe_yza.py" @argsList
} else {
    & $PythonExe ".\scripts\test_yza.py" @argsList --max-pages $MaxPages
}
exit $LASTEXITCODE
