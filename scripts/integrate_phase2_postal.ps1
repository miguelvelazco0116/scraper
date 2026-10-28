param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [switch]$Preview
)
$ErrorActionPreference = "Stop"
Set-Location $ProjectDir

if (-not $PythonExe -and $env:VIRTUAL_ENV) {
    $candidate = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
    if (Test-Path $candidate) { $PythonExe = $candidate }
}
if (-not $PythonExe) {
    $candidate = "C:\Proyectos\venvs\scraper\Scripts\python.exe"
    if (Test-Path $candidate) { $PythonExe = $candidate }
}
if (-not $PythonExe -or -not (Test-Path $PythonExe)) {
    throw "Activa el entorno scraper antes de integrar Fase 2."
}

Write-Host "FASE 2 / INTEGRACION DE CAPTURAS EXISTENTES"
Write-Host "NO se inicia Chrome ni se descarga Soriana de nuevo."
Write-Host "Origen 1: Farmacias Guadalajara, Excel validado del dia."
Write-Host "Origen 2: Soriana, cuatro Excel auditados 24/24 CP/categoria."
Write-Host "Master: solo master activo del dia, con Fase 1 COMPLETE."
$day = Get-Date -Format "yyyy-MM-dd"
$argsList = @(".\scripts\integrate_phase2_postal.py", "--day", $day)
if ($Preview) {
    $argsList += "--preview"
    Write-Host "MODO PREVIEW: SIN ESCRITURAS."
} else {
    $argsList += "--commit"
    Write-Host "MODO COMMIT: respaldo + integracion + auditoria + rollback."
}
& $PythonExe @argsList
exit $LASTEXITCODE
