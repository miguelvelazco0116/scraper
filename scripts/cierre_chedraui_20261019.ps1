param(
    [switch]$Commit,
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
    throw "Python valido no disponible."
}

$mode = if ($Commit) { "--commit" } else { "--preview" }
Write-Host "CIERRE CHEDRAUI 2026-10-19 - 3 SUCURSALES"
Write-Host "Modo: $mode"
& $PythonExe ".\scripts\cierre_chedraui_20261019.py" "$mode"
if ($LASTEXITCODE -ne 0) {
    throw "Cierre Chedraui rechazado (exit=$LASTEXITCODE). El master no se da por actualizado."
}
