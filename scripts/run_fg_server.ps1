param(
    [string]$PythonExe = "C:\VM-py\venvs\miguel_velazco\Scripts\python.exe",
    [string]$ProjectDir = "C:\VM-py\notebooks\miguel_velazco\scraper",
    [string]$TaskName = "Scraper-FarmaciasGuadalajara",
    [int]$Timeout = 1200
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $PythonExe)) {
    throw "No existe Python: $PythonExe"
}

if (-not (Test-Path $ProjectDir)) {
    throw "No existe el proyecto: $ProjectDir"
}

Set-Location $ProjectDir

Write-Host "============================================================"
Write-Host "Farmacias Guadalajara - Chrome - validacion y ejecucion"
Write-Host "============================================================"
Write-Host ""

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
if ($task.Principal.LogonType -ne "Interactive") {
    throw "La tarea $TaskName no usa LogonType Interactive."
}

function Get-AccountLeaf([string]$Account) {
    if ([string]::IsNullOrWhiteSpace($Account)) {
        return ""
    }
    $parts = $Account -split '[\\/]'
    return $parts[-1].Trim()
}

$taskUserLeaf = Get-AccountLeaf $task.Principal.UserId

$interactive = Get-Process -Name explorer -IncludeUserName -ErrorAction SilentlyContinue |
    Where-Object {
        $_.SessionId -gt 0 -and
        -not [string]::IsNullOrWhiteSpace($_.UserName) -and
        (Get-AccountLeaf $_.UserName) -ieq $taskUserLeaf
    } |
    Sort-Object SessionId -Descending |
    Select-Object -First 1

if (-not $interactive) {
    $detectedUsers = Get-Process -Name explorer -IncludeUserName -ErrorAction SilentlyContinue |
        Where-Object {
            $_.SessionId -gt 0 -and
            -not [string]::IsNullOrWhiteSpace($_.UserName)
        } |
        Select-Object -ExpandProperty UserName -Unique

    $detectedText = if ($detectedUsers) {
        $detectedUsers -join ", "
    } else {
        "(ninguno)"
    }

    throw "El usuario de la tarea ($($task.Principal.UserId)) no tiene una sesion interactiva compatible. Usuarios explorer detectados: $detectedText"
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
    throw "Google Chrome no esta instalado en una ruta estandar para el usuario interactivo."
}

Write-Host "Tarea       : $TaskName"
Write-Host "Task User   : $($task.Principal.UserId)"
Write-Host "Interactive : $($interactive.UserName) / Session $($interactive.SessionId)"
Write-Host "Chrome      : $chrome"
Write-Host "Python      : $PythonExe"
Write-Host ""

Write-Host "[1/2] Ejecutando pruebas locales..."
& $PythonExe -m pytest tests/test_farmacias_guadalajara.py tests/test_fg_worker.py tests/test_fg_runner.py -q

if ($LASTEXITCODE -ne 0) {
    throw "Las pruebas locales fallaron. No se iniciara el scraping."
}

Write-Host ""
Write-Host "[2/2] Ejecutando preflight real y scraping completo con Chrome..."
& $PythonExe .\scripts\run_farmacias_guadalajara.py --category all --max-load-more 100 --timeout $Timeout --browser-channel chrome-cdp --min-row-coverage 1.0 --attempts 2 --retry-pause 10

if ($LASTEXITCODE -ne 0) {
    throw "Farmacias Guadalajara termino con exit_code=$LASTEXITCODE"
}

Write-Host ""
Write-Host "============================================================"
Write-Host "FARMACIAS GUADALAJARA COMPLETADO Y VALIDADO AL 100%"
Write-Host "Browser    : Google Chrome"
Write-Host "Concentrado: $ProjectDir\output\concentrado_scraper.xlsx"
Write-Host "============================================================"
