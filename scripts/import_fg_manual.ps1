param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [string]$InputFile = ""
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectDir

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    if ($env:VIRTUAL_ENV) {
        $candidate = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
        if (Test-Path $candidate) { $PythonExe = $candidate }
    }
}

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $candidate = Join-Path $ProjectDir ".venv\Scripts\python.exe"
    if (Test-Path $candidate) { $PythonExe = $candidate }
}

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd) { $PythonExe = $cmd.Source }
}

if ([string]::IsNullOrWhiteSpace($PythonExe) -or -not (Test-Path $PythonExe)) {
    throw "No se encontro Python valido."
}

if ([string]::IsNullOrWhiteSpace($InputFile)) {
    $downloads = Join-Path $env:USERPROFILE "Downloads"
    $latest = Get-ChildItem $downloads -Filter "farmacias_guadalajara_*.json" -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1

    if (-not $latest) {
        throw "No se encontro un JSON de Farmacias Guadalajara en Downloads."
    }
    $InputFile = $latest.FullName
}

Write-Host "Python : $PythonExe"
Write-Host "Input  : $InputFile"
Write-Host ""

& $PythonExe .\scripts\import_fg_manual.py --input $InputFile
if ($LASTEXITCODE -ne 0) {
    throw "La importacion termino con exit_code=$LASTEXITCODE"
}
