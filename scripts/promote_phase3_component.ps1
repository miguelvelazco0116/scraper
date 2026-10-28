param(
    [Parameter(Mandatory=$true)]
    [string]$Source,

    [Parameter(Mandatory=$true)]
    [string]$Retailer,

    [Parameter(Mandatory=$true)]
    [string]$CategoryId,

    [string]$CaptureDate = "",

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

$ArgsList = @(
    ".\scripts\promote_phase3_component.py",
    "--source", $Source,
    "--retailer", $Retailer,
    "--category-id", $CategoryId
)
if (-not [string]::IsNullOrWhiteSpace($CaptureDate)) {
    $ArgsList += @("--capture-date", $CaptureDate)
}

& $PythonExe @ArgsList
exit $LASTEXITCODE
