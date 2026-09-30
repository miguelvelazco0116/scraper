param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$VenvDir = ".venv"
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectDir

Write-Host "============================================================"
Write-Host "Farmacias Guadalajara - setup PC personal"
Write-Host "============================================================"

$launcher = $null
$launcherArgs = @()

if (Get-Command py.exe -ErrorAction SilentlyContinue) {
    $launcher = "py.exe"
    $launcherArgs = @("-3")
}
elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
    $launcher = "python.exe"
}
else {
    throw "No se encontro Python en PATH. Instala Python 3 y vuelve a ejecutar."
}

$venvPath = Join-Path $ProjectDir $VenvDir
$venvPython = Join-Path $venvPath "Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "Creando ambiente virtual: $venvPath"
    & $launcher @launcherArgs -m venv $venvPath
    if ($LASTEXITCODE -ne 0) {
        throw "No se pudo crear el ambiente virtual."
    }
}

$programFilesX86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
$chromeCandidates = @()
if ($env:ProgramFiles) {
    $chromeCandidates += Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"
}
if ($programFilesX86) {
    $chromeCandidates += Join-Path $programFilesX86 "Google\Chrome\Application\chrome.exe"
}
if ($env:LOCALAPPDATA) {
    $chromeCandidates += Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe"
}

$chrome = $chromeCandidates |
    Where-Object { Test-Path $_ } |
    Select-Object -First 1

if (-not $chrome) {
    throw "Google Chrome no esta instalado en una ruta estandar."
}

Write-Host "Python venv : $venvPython"
Write-Host "Chrome      : $chrome"
Write-Host ""
Write-Host "Instalando dependencias..."

& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "Fallo al actualizar pip."
}

& $venvPython -m pip install -r .\requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw "Fallo al instalar requirements.txt."
}

Write-Host ""
Write-Host "Validando imports y tests..."
& $venvPython -m pytest tests/test_farmacias_guadalajara.py tests/test_fg_worker.py tests/test_fg_runner.py -q
if ($LASTEXITCODE -ne 0) {
    throw "Los tests fallaron durante el setup."
}

Write-Host ""
Write-Host "SETUP LOCAL COMPLETADO"
Write-Host "Siguiente paso:"
Write-Host "  & .\scripts\run_fg_local.ps1"
