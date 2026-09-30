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
Write-Host "Farmacias Guadalajara - validacion y ejecucion"
Write-Host "============================================================"
Write-Host ""

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
if ($task.Principal.LogonType -ne "Interactive") {
    throw "La tarea $TaskName no usa LogonType Interactive."
}

$interactive = Get-Process -Name explorer -IncludeUserName -ErrorAction SilentlyContinue |
    Where-Object {
        $_.SessionId -gt 0 -and
        -not [string]::IsNullOrWhiteSpace($_.UserName) -and
            -not (
                $_.UserName -like 'NT AUTHORITY\*' -or
                $_.UserName -like 'Window Manager\*'
            )
    } |
    Sort-Object SessionId -Descending |
    Select-Object -First 1

if (-not $interactive) {
    throw "No hay una sesion interactiva de Windows. Inicia sesion por RDP y vuelve a ejecutar."
}

Write-Host "Tarea       : $TaskName"
Write-Host "Task User   : $($task.Principal.UserId)"
Write-Host "Interactive : $($interactive.UserName) / Session $($interactive.SessionId)"
Write-Host "Python      : $PythonExe"
Write-Host ""

Write-Host "[1/2] Ejecutando pruebas locales..."
& $PythonExe -m pytest tests/test_farmacias_guadalajara.py tests/test_fg_worker.py tests/test_fg_runner.py -q

if ($LASTEXITCODE -ne 0) {
    throw "Las pruebas locales fallaron. No se iniciara el scraping."
}

Write-Host ""
Write-Host "[2/2] Ejecutando preflight real y scraping completo..."
& $PythonExe .\scripts\run_farmacias_guadalajara.py --category all --max-load-more 100 --timeout $Timeout --browser-channel msedge-cdp

if ($LASTEXITCODE -ne 0) {
    throw "Farmacias Guadalajara termino con exit_code=$LASTEXITCODE"
}

Write-Host ""
Write-Host "============================================================"
Write-Host "FARMACIAS GUADALAJARA COMPLETADO Y VALIDADO"
Write-Host "Concentrado: $ProjectDir\output\concentrado_scraper.xlsx"
Write-Host "============================================================"
