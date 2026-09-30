param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [string]$Category = "all",
    [int]$Timeout = 1200,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectDir

Write-Host "============================================================"
Write-Host "Farmacias Guadalajara - PC personal / Microsoft Edge"
Write-Host "============================================================"
Write-Host ""

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    if ($env:VIRTUAL_ENV) {
        $candidate = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
        if (Test-Path $candidate) {
            $PythonExe = $candidate
        }
    }

    if ([string]::IsNullOrWhiteSpace($PythonExe)) {
        $candidate = Join-Path $ProjectDir ".venv\Scripts\python.exe"
        if (Test-Path $candidate) {
            $PythonExe = $candidate
        }
    }

    if ([string]::IsNullOrWhiteSpace($PythonExe)) {
        $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($pythonCommand) {
            $PythonExe = $pythonCommand.Source
        }
    }
}

if ([string]::IsNullOrWhiteSpace($PythonExe) -or -not (Test-Path $PythonExe)) {
    throw "No se encontro un Python valido. Activa tu ambiente virtual o usa -PythonExe."
}

$edgeCandidates = @(
    "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "C:\Program Files\Microsoft\Edge\Application\msedge.exe"
)

$edge = $edgeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $edge) {
    throw "Microsoft Edge no esta instalado en una ruta estandar."
}

$env:FG_BROWSER_EXECUTABLE = $edge
$env:FG_BROWSER_CHANNEL = "msedge"
$env:FG_DISABLE_HTTP2 = "0"
$env:FG_DISABLE_QUIC = "0"
Remove-Item Env:FG_CDP_URL -ErrorAction SilentlyContinue
Remove-Item Env:FG_USER_AGENT -ErrorAction SilentlyContinue
Remove-Item Env:FG_GRID_REQUEST_FALLBACK -ErrorAction SilentlyContinue

Write-Host "Python  : $PythonExe"
Write-Host "Edge    : $edge"
Write-Host "Modo    : local, headed, sin Task Scheduler, sin CDP"
Write-Host "Category: $Category"
Write-Host ""

if (-not $SkipTests) {
    Write-Host "[1/2] Ejecutando pruebas locales..."
    & $PythonExe -m pytest tests/test_farmacias_guadalajara.py tests/test_fg_worker.py tests/test_fg_runner.py -q
    if ($LASTEXITCODE -ne 0) {
        throw "Las pruebas locales fallaron. No se iniciara el scraping."
    }
} else {
    Write-Host "[1/2] Tests omitidos por parametro -SkipTests"
}

Write-Host ""
Write-Host "[2/2] Ejecutando preflight grafico y scraping con Edge..."
& $PythonExe .\scripts\run_farmacias_guadalajara.py --execution-mode local --browser-channel msedge --category $Category --max-load-more 100 --timeout $Timeout --min-row-coverage 1.0 --attempts 2 --retry-pause 10

if ($LASTEXITCODE -ne 0) {
    throw "Farmacias Guadalajara termino con exit_code=$LASTEXITCODE"
}

Write-Host ""
Write-Host "============================================================"
Write-Host "FARMACIAS GUADALAJARA LOCAL COMPLETADO Y VALIDADO"
Write-Host "Browser    : Microsoft Edge"
Write-Host "Concentrado: $ProjectDir\output\concentrado_scraper.xlsx"
Write-Host "Diagnostico: $ProjectDir\diagnostics"
Write-Host "============================================================"
