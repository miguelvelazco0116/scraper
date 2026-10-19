param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("chedraui-polanco", "chedraui-tepeyac", "chedraui-cancun-kabah", "chedraui-puebla-centro-sur")]
    [string]$Store,
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
    throw "No se encontro Python valido."
}

$mode = if ($Commit) { "--commit" } else { "--preview" }
Write-Host "CHEDRAUI - INTEGRACION POR SUCURSAL"
Write-Host "Tienda: $Store"
Write-Host "Modo: $mode"
& $PythonExe ".\scripts\integrate_chedraui_store.py" "--store" "$Store" "$mode"
if ($LASTEXITCODE -ne 0) {
    throw "Integracion Chedraui fallida (exit=$LASTEXITCODE); revisar diagnostico."
}
