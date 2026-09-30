param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$VenvDir = ".venv",
    [string]$PythonExe = ""
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

if ([string]::IsNullOrWhiteSpace($PythonExe) -and $env:VIRTUAL_ENV) {
    $activePython = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
    if (Test-Path $activePython) {
        $PythonExe = $activePython
    }
}

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $venvPath = Join-Path $ProjectDir $VenvDir
    $PythonExe = Join-Path $venvPath "Scripts\python.exe"

    if (-not (Test-Path $PythonExe)) {
        Write-Host "Creando ambiente virtual: $venvPath"
        & $launcher @launcherArgs -m venv $venvPath
        if ($LASTEXITCODE -ne 0) {
            throw "No se pudo crear el ambiente virtual."
        }
    }
}

if (-not (Test-Path $PythonExe)) {
    throw "Python del ambiente virtual no encontrado: $PythonExe"
}

$edgeCandidates = @(
    "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "C:\Program Files\Microsoft\Edge\Application\msedge.exe"
)

$edge = $edgeCandidates |
    Where-Object { Test-Path $_ } |
    Select-Object -First 1

if (-not $edge) {
    throw "Microsoft Edge no esta instalado en una ruta estandar."
}

Write-Host "Python venv : $PythonExe"
Write-Host "Edge        : $edge"
Write-Host ""
Write-Host "Instalando dependencias..."

& $PythonExe -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "Fallo al actualizar pip."
}

& $PythonExe -m pip install -r .\requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw "Fallo al instalar requirements.txt."
}

Write-Host ""
Write-Host "Validando imports y tests..."
& $PythonExe -m pytest tests/test_farmacias_guadalajara.py tests/test_fg_worker.py tests/test_fg_runner.py -q
if ($LASTEXITCODE -ne 0) {
    throw "Los tests fallaron durante el setup."
}

Write-Host ""
Write-Host "SETUP LOCAL COMPLETADO"
Write-Host "Siguiente paso:"
Write-Host "  & .\scripts\run_fg_local.ps1"
