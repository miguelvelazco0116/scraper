param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [switch]$SkipIntegration,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectDir

# This is the ONLY "Todas las tiendas" execution path in Streamlit Fase 2.
# It intentionally does not call the legacy run_phase2_production.ps1,
# which would scrape Soriana a second time without its postal rotation.
if (-not $SkipIntegration) {
    throw "Fase 2 desde Streamlit es SOLO CAPTURA. Integracion productiva deshabilitada."
}

Write-Host "FASE 2 - CAPTURA UNICA DESDE STREAMLIT"
Write-Host "1. Farmacias Guadalajara: 4 categorias / una ejecucion"
Write-Host "2. Soriana: 4 CP x 6 categorias / una ejecucion, checkpoints del dia"
Write-Host "Códigos postales Soriana: 83420, 44220, 54030, 97245"
Write-Host "Master productivo: SIN CAMBIOS"

if ($DryRun) {
    Write-Host "DRY RUN: no se inicia navegador, no se descargan productos."
    exit 0
}

if (-not $PythonExe -and $env:VIRTUAL_ENV) {
    $candidate = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
    if (Test-Path $candidate) { $PythonExe = $candidate }
}
if (-not $PythonExe) {
    $candidate = "C:\Proyectos\venvs\scraper\Scripts\python.exe"
    if (Test-Path $candidate) { $PythonExe = $candidate }
}
if (-not $PythonExe -or -not (Test-Path $PythonExe)) {
    throw "Activa el entorno scraper antes de ejecutar Fase 2."
}

$fgScript = Join-Path $PSScriptRoot "test_farmacias_guadalajara_phase2.ps1"
$sorianaScript = Join-Path $PSScriptRoot "test_soriana_cp_rotation.ps1"
if (-not (Test-Path $fgScript) -or -not (Test-Path $sorianaScript)) {
    throw "Falta un runner validado de Fase 2."
}

Write-Host ""
Write-Host "[1/2] FARMACIAS GUADALAJARA"
& $fgScript -Category all -ProjectDir $ProjectDir -PythonExe $PythonExe
$fgCode = $LASTEXITCODE
Write-Host "Guadalajara exit_code: $fgCode"

Write-Host ""
Write-Host "[2/2] SORIANA - ROTACION CP (NO legacy)"
& $sorianaScript -PostalCode all -Category all -ProjectDir $ProjectDir -PythonExe $PythonExe
$sorianaCode = $LASTEXITCODE
Write-Host "Soriana CP exit_code: $sorianaCode"

# Exit 0 is not enough: the independent read-only audit must establish 24/24
# COMPLETE from the four Excel files for the same local capture date.
$auditCode = 2
if ($sorianaCode -eq 0) {
    $day = Get-Date -Format "yyyy-MM-dd"
    Write-Host ""
    Write-Host "AUDITORIA INDEPENDIENTE SORIANA: $day"
    & $PythonExe ".\scripts\audit_soriana_cp_rotation.py" --day $day
    $auditCode = $LASTEXITCODE
}
if ($fgCode -ne 0 -or $sorianaCode -ne 0 -or $auditCode -ne 0) {
    Write-Warning "FASE 2 no validada completamente. FG=$fgCode; Soriana=$sorianaCode; AuditoriaCP=$auditCode."
    Write-Host "Se conservaron las capturas y checkpoints disponibles para reintentar."
    Write-Host "Master productivo: SIN CAMBIOS"
    exit 2
}

Write-Host "FASE 2 CAPTURA VALIDADA: Guadalajara COMPLETE y Soriana 24/24 COMPLETE"
Write-Host "Sin integracion ni segunda captura Soriana. MASTER SIN CAMBIOS."
exit 0
