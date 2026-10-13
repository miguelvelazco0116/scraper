param(
    [Parameter(Mandatory = $true)]
    [ValidateSet(1, 2, 3)]
    [int]$Phase,
    [ValidateSet("", "Bodega Aurrera", "Walmart")]
    [string]$Retailer = "",
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = ""
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

& $PythonExe @ArgsList
exit $LASTEXITCODE
