param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$Category = "all",
    [int]$Timeout = 1200,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectDir

Write-Host "============================================================"
Write-Host "Farmacias Guadalajara - PC personal / Google Chrome"
Write-Host "============================================================"
Write-Host ""

$venvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw "No existe .venv. Ejecuta primero: & .\scripts\setup_fg_local.ps1"
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

$chrome = $chromeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) {
    throw "Google Chrome no esta instalado en una ruta estandar."
}

$env:FG_BROWSER_EXECUTABLE = $chrome
$env:FG_BROWSER_CHANNEL = "chrome"
$env:FG_DISABLE_HTTP2 = "0"
$env:FG_DISABLE_QUIC = "0"
Remove-Item Env:FG_CDP_URL -ErrorAction SilentlyContinue
Remove-Item Env:FG_USER_AGENT -ErrorAction SilentlyContinue

Write-Host "Python  : $venvPython"
Write-Host "Chrome  : $chrome"
Write-Host "Modo    : local, headed, sin Task Scheduler, sin CDP"
Write-Host "Category: $Category"
Write-Host ""

if (-not $SkipTests) {
    Write-Host "[1/2] Ejecutando pruebas locales..."
    & $venvPython -m pytest tests/test_farmacias_guadalajara.py tests/test_fg_worker.py tests/test_fg_runner.py -q
    if ($LASTEXITCODE -ne 0) {
        throw "Las pruebas locales fallaron. No se iniciara el scraping."
    }
} else {
    Write-Host "[1/2] Tests omitidos por parametro -SkipTests"
}

Write-Host ""
Write-Host "[2/2] Ejecutando preflight grafico y scraping con Chrome..."
& $venvPython .\scripts\run_farmacias_guadalajara.py --execution-mode local --browser-channel chrome --category $Category --max-load-more 100 --timeout $Timeout --min-row-coverage 1.0 --attempts 2 --retry-pause 10

if ($LASTEXITCODE -ne 0) {
    throw "Farmacias Guadalajara termino con exit_code=$LASTEXITCODE"
}

Write-Host ""
Write-Host "============================================================"
Write-Host "FARMACIAS GUADALAJARA LOCAL COMPLETADO Y VALIDADO"
Write-Host "Browser    : Google Chrome"
Write-Host "Concentrado: $ProjectDir\output\concentrado_scraper.xlsx"
Write-Host "Diagnostico: $ProjectDir\diagnostics"
Write-Host "============================================================"
