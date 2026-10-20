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
    throw "No se encontro Python para generar el historico Chedraui."
}

$mode = if ($Commit) { "--commit" } else { "--preview" }
Write-Host "CHEDRAUI - HISTORICO AUDITADO 19/20 OCT 2026"
Write-Host "Modo: $mode"
& $PythonExe ".\scripts\export_chedraui_historico.py" "$mode"
if ($LASTEXITCODE -ne 0) {
    throw "Exportacion Chedraui rechazada (exit=$LASTEXITCODE). Sin cambios en master."
}
