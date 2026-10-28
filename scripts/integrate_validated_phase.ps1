param(
    [Parameter(Mandatory = $true)]
    [ValidateSet(1, 2, 3, 4, 5)]
    [int]$Phase,
    [ValidateSet("", "Bodega Aurrera", "Walmart", "Farmacias San Pablo", "Farmacias Benavides", "Farmacias Similares", "HEB", "Sam's Club", "Mayorista Duero")]
    [string]$Retailer = "",
    [switch]$AllowPartialPrevious,
    [string]$ProjectDir = "",
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($ProjectDir)) {
    $ScriptFile = $MyInvocation.MyCommand.Path
    if ([string]::IsNullOrWhiteSpace($ScriptFile)) { throw "No se pudo determinar la ruta del proyecto. Especifica -ProjectDir." }
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

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd) { $PythonExe = $cmd.Source }
}

if ([string]::IsNullOrWhiteSpace($PythonExe) -or -not (Test-Path $PythonExe)) {
    throw "No se encontro Python valido."
}

Write-Host "========================================================================"
Write-Host "INTEGRAR FASE VALIDADA AL CONCENTRADO"
Write-Host "========================================================================"
Write-Host "Fase   : $Phase"
if (-not [string]::IsNullOrWhiteSpace($Retailer)) {
    Write-Host "Retailer: $Retailer"
}
Write-Host "Python : $PythonExe"
Write-Host ""

$ArgsList = @(".\scripts\integrate_validated_phase.py", "--phase", "$Phase")
if (-not [string]::IsNullOrWhiteSpace($Retailer)) {
    $ArgsList += @("--retailer", $Retailer)
}
if ($AllowPartialPrevious) {
    $ArgsList += "--allow-partial-previous"
}

& $PythonExe @ArgsList
exit $LASTEXITCODE
