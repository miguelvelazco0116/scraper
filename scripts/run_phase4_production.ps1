param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [string]$CdpUrl = "",
    [ValidateRange(1, 100)]
    [int]$SanPabloMaxPages = 20,
    [ValidateRange(1, 200)]
    [int]$BenavidesMaxPages = 60,
    [switch]$HeadedBenavides,
    [switch]$SkipIntegration
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
    $candidate = Join-Path $ProjectDir ".venv\Scripts\python.exe"
    if (Test-Path $candidate) { $PythonExe = $candidate }
}
if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd) { $PythonExe = $cmd.Source }
}
if ([string]::IsNullOrWhiteSpace($PythonExe) -or -not (Test-Path $PythonExe)) {
    throw "No se encontro Python valido para Fase 4."
}

# Refuse to change a master from another day or a missing active master.
$pointerPath = Join-Path $ProjectDir "output\checkpoints_validos\current_master.json"
if (-not (Test-Path $pointerPath)) {
    throw "Falta current_master.json. No se inicia Fase 4."
}
$pointer = Get-Content -LiteralPath $pointerPath -Raw | ConvertFrom-Json
$today = Get-Date -Format "yyyy-MM-dd"
if ($pointer.capture_date -ne $today -or -not (Test-Path $pointer.master)) {
    throw "Master activo ausente o de otra fecha. No se inicia Fase 4."
}

if ([string]::IsNullOrWhiteSpace($CdpUrl)) {
    $chromeData = Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data"
    $portFiles = @()
    if (Test-Path $chromeData) {
        $portFiles = @(Get-ChildItem -Path $chromeData -Filter "DevToolsActivePort" -File -Recurse -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending)
    }
    if ($portFiles.Count -eq 0) {
        Write-Host "ERROR: habilita Chrome CDP. Abre https://www.farmaciasanpablo.com.mx/"
        Write-Host "Luego chrome://inspect/#remote-debugging y activa Allow remote debugging."
        exit 11
    }
    $lines = @(Get-Content -LiteralPath $portFiles[0].FullName)
    if ($lines.Count -lt 2) {
        Write-Host "ERROR: DevToolsActivePort incompleto."
        exit 12
    }
    $CdpUrl = "ws://127.0.0.1:$($lines[0].Trim())$($lines[1].Trim())"
}

$logDir = Join-Path $ProjectDir "diagnostics\phase4_production"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("phase4_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")
Start-Transcript -Path $log -Force | Out-Null

$sanCode = 99
$benCode = 99
$integrateCode = 99
$auditCode = 99
$phase4Complete = $false

try {
    Write-Host "========================================================================"
    Write-Host "FASE 4 COMPLETA - CAPTURA, INTEGRACION Y AUDITORIA"
    Write-Host "========================================================================"
    Write-Host "Retailers : Farmacias San Pablo (4) + Farmacias Benavides (3)"
    Write-Host "Master    : $($pointer.master)"
    Write-Host "Python    : $PythonExe"
    Write-Host "Log       : $log"
    Write-Host "Integrar  : $(-not $SkipIntegration)"
    Write-Host "Capturas  : secuenciales; reusan categorias validas del dia"
    Write-Host ""

    Write-Host "========================================================================"
    Write-Host "[1/4] FARMACIAS SAN PABLO - 4 CATEGORIAS"
    Write-Host "========================================================================"
    & $PythonExe ".\scripts\run_san_pablo_full.py" --category all --max-pages $SanPabloMaxPages --cdp-url $CdpUrl
    $sanCode = $LASTEXITCODE
    Write-Host "SAN PABLO exit_code=$sanCode"
    Write-Host ""

    Write-Host "========================================================================"
    Write-Host "[2/4] FARMACIAS BENAVIDES - 3 CATEGORIAS"
    Write-Host "========================================================================"
    $benArgs = @(".\scripts\test_benavides.py", "--category", "all", "--max-pages", "$BenavidesMaxPages")
    if ($HeadedBenavides) { $benArgs += "--headed" }
    & $PythonExe @benArgs
    $benCode = $LASTEXITCODE
    Write-Host "BENAVIDES exit_code=$benCode"
    Write-Host ""

    if (-not $SkipIntegration) {
        Write-Host "========================================================================"
        Write-Host "[3/4] INTEGRACION INCREMENTAL - SOLO RETAILERS COMPLETOS"
        Write-Host "========================================================================"
        & ".\scripts\integrate_complete_retailers.ps1" -Phase 4 -ProjectDir $ProjectDir -PythonExe $PythonExe
        $integrateCode = $LASTEXITCODE
        Write-Host "INTEGRACION exit_code=$integrateCode"
    } else {
        $integrateCode = 0
        Write-Host "[3/4] INTEGRACION OMITIDA POR -SkipIntegration"
    }

    Write-Host ""
    Write-Host "========================================================================"
    Write-Host "[4/4] AUDITORIA DEL MASTER - SOLO LECTURA"
    Write-Host "========================================================================"
    & $PythonExe ".\scripts\audit_master_phases.py"
    $auditCode = $LASTEXITCODE

    if (-not $SkipIntegration -and $auditCode -eq 0) {
        # Exit code 0 from the integrator can also mean partial success.
        # Only the current master pointer can confirm Fase 4 is closed.
        $updatedPointer = Get-Content -LiteralPath $pointerPath -Raw | ConvertFrom-Json
        $phase4Complete = @( $updatedPointer.completed_phases | Where-Object { $_ -eq 4 } ).Count -gt 0
    }

    Write-Host ""
    Write-Host "========================================================================"
    Write-Host "RESUMEN EJECUCION FASE 4"
    Write-Host "========================================================================"
    Write-Host "San Pablo  : $sanCode"
    Write-Host "Benavides  : $benCode"
    Write-Host "Integracion: $integrateCode"
    Write-Host "Auditoria  : $auditCode"
    Write-Host "Fase 4    : $(if ($SkipIntegration) { 'SIN INTEGRACION (-SkipIntegration)' } elseif ($phase4Complete) { 'COMPLETE' } else { 'PARTIAL_VALIDATED/PENDING' })"
    Write-Host "Log        : $log"
} finally {
    try { Stop-Transcript | Out-Null } catch {}
}

if ($sanCode -eq 0 -and $benCode -eq 0 -and $integrateCode -eq 0 -and $auditCode -eq 0 -and ($phase4Complete -or $SkipIntegration)) {
    exit 0
}
Write-Warning "Fase 4 con pendientes u observaciones. Revisa el reporte; no se integran retailers incompletos."
exit 2
