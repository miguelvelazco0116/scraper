param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [string]$CdpUrl = "",
    [ValidateRange(0, 5)]
    [int]$RetryFailedAtEnd = 1,
    [ValidateRange(2, 100)]
    [int]$DueroMaxPages = 30,
    [ValidateRange(0, 15)]
    [double]$DueroDelaySeconds = 1,
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
    $candidate = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    if ($candidate -and (Test-Path $candidate)) { $PythonExe = $candidate }
}
if (-not $PythonExe -or -not (Test-Path $PythonExe)) {
    throw "No se encontro Python valido para Fase 5."
}

$pointerPath = Join-Path $ProjectDir "output\checkpoints_validos\current_master.json"
if (-not (Test-Path $pointerPath)) { throw "Falta current_master.json. No se inicia la Fase 5." }
$pointer = Get-Content -LiteralPath $pointerPath -Raw | ConvertFrom-Json
$today = Get-Date -Format "yyyy-MM-dd"
if ($pointer.capture_date -ne $today -or -not $pointer.master -or -not (Test-Path $pointer.master)) {
    throw "El master activo no corresponde a hoy o no existe. No se inicia Fase 5."
}
$requiredPreviousPhases = @(1, 2, 3, 4)
$completed = @($pointer.completed_phases)
foreach ($phase in $requiredPreviousPhases) {
    if ($completed -notcontains $phase) {
        throw "La fase $phase no esta COMPLETE en current_master.json. No se inicia Fase 5."
    }
}

$logDir = Join-Path $ProjectDir "diagnostics\phase5_production"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$log = Join-Path $logDir ("phase5_" + $stamp + ".log")
Start-Transcript -Path $log -Force | Out-Null

$codes = @{}
$phase5Complete = $false
try {
    Write-Host "========================================================================"
    Write-Host "FASE 5 - EJECUCION DE TODOS LOS RETAILERS"
    Write-Host "========================================================================"
    Write-Host "Fecha     : $today"
    Write-Host "Master    : $($pointer.master)"
    Write-Host "Retailers : Similares (2) + HEB (2) + Sam's Club (3) + Duero (6)"
    Write-Host "Integrar  : $(if ($SkipIntegration) { 'NO' } else { 'SI, SOLO RETAILERS VERIFICADOS' })"
    Write-Host "Duero     : sin aprobacion de catalogo online / sin certificacion CP"
    Write-Host "Log       : $log"
    Write-Host ""

    # All four scrapers are independent. A partial capture must never stop
    # later retailers from being attempted.
    Write-Host "========================================================================"
    Write-Host "[1/8] FARMACIAS SIMILARES"
    Write-Host "========================================================================"
    & $PythonExe ".\scripts\test_farmacias_similares.py" --category all --retry-failed-at-end $RetryFailedAtEnd
    $codes["similares"] = $LASTEXITCODE
    Write-Host "Similares exit_code=$($codes['similares'])"
    Write-Host ""

    if ([string]::IsNullOrWhiteSpace($CdpUrl)) {
        $chromeData = Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data"
        if (Test-Path $chromeData) {
            $ports = @(Get-ChildItem -Path $chromeData -Filter "DevToolsActivePort" -File -Recurse -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending)
            if ($ports.Count -gt 0) {
                $lines = @(Get-Content -LiteralPath $ports[0].FullName)
                if ($lines.Count -ge 2) {
                    $CdpUrl = "ws://127.0.0.1:$($lines[0].Trim())$($lines[1].Trim())"
                }
            }
        }
    }

    Write-Host "========================================================================"
    Write-Host "[2/8] HEB"
    Write-Host "========================================================================"
    if ([string]::IsNullOrWhiteSpace($CdpUrl)) {
        Write-Warning "HEB omitido: Chrome CDP no disponible. Abre Chrome, configura una tienda HEB y habilita remote debugging."
        $codes["heb"] = 11
    } else {
        & $PythonExe ".\scripts\test_heb.py" --category all --retry-failed-at-end $RetryFailedAtEnd --cdp-url $CdpUrl
        $codes["heb"] = $LASTEXITCODE
    }
    Write-Host "HEB exit_code=$($codes['heb'])"
    Write-Host ""

    Write-Host "========================================================================"
    Write-Host "[3/8] SAM'S CLUB"
    Write-Host "========================================================================"
    if ([string]::IsNullOrWhiteSpace($CdpUrl)) {
        Write-Warning "Sam's Club omitido: Chrome CDP no disponible."
        $codes["sams"] = 11
    } else {
        & $PythonExe ".\scripts\test_sams_club.py" --retry-failed-at-end $RetryFailedAtEnd --cdp-url $CdpUrl
        $codes["sams"] = $LASTEXITCODE
    }
    Write-Host "Sam's Club exit_code=$($codes['sams'])"
    Write-Host ""

    Write-Host "========================================================================"
    Write-Host "[4/8] MAYORISTA DUERO: DOBLE PAGINACION 50 / 250"
    Write-Host "========================================================================"
    & $PythonExe ".\scripts\test_duero_mayoreo.py" --category all --max-pages $DueroMaxPages --delay-seconds $DueroDelaySeconds
    $codes["duero"] = $LASTEXITCODE
    Write-Host "Duero exit_code=$($codes['duero'])"
    Write-Host ""

    Write-Host "========================================================================"
    Write-Host "[5/8] AUDITORIAS SECUNDARIAS DUERO"
    Write-Host "========================================================================"
    if ($codes["duero"] -eq 0) {
        & $PythonExe ".\scripts\audit_duero_phase5.py"
        $codes["duero_packaging"] = $LASTEXITCODE
        & $PythonExe ".\scripts\audit_duero_storefront.py" --delay-seconds $DueroDelaySeconds
        $codes["duero_storefront"] = $LASTEXITCODE
    } else {
        $codes["duero_packaging"] = 2
        $codes["duero_storefront"] = 2
        Write-Warning "Auditorias Duero no ejecutadas: la captura no paso los controles de paginacion."
    }

    Write-Host ""
    Write-Host "========================================================================"
    Write-Host "[6/8] VISTA PREVIA INTEGRACION FASE 5 - SOLO LECTURA"
    Write-Host "========================================================================"
    & $PythonExe ".\scripts\integrate_complete_retailers.py" --phase 5 --preview
    $codes["preview"] = $LASTEXITCODE

    Write-Host ""
    Write-Host "========================================================================"
    Write-Host "[7/8] INTEGRACION DE RETAILERS COMPLETOS DEL DIA"
    Write-Host "========================================================================"
    if ($SkipIntegration) {
        $codes["integration"] = 0
        Write-Host "Integracion omitida por -SkipIntegration."
    } elseif ($codes["preview"] -ne 0) {
        $codes["integration"] = 2
        Write-Warning "No se integra: la vista previa devolvio un error."
    } else {
        # No --approve-duero-online: never silently attest postal prices,
        # store context, or packaging based on public Shopify data alone.
        & $PythonExe ".\scripts\integrate_complete_retailers.py" --phase 5
        $codes["integration"] = $LASTEXITCODE
    }

    Write-Host ""
    Write-Host "========================================================================"
    Write-Host "[8/8] AUDITORIA DEL MASTER COMPLETO"
    Write-Host "========================================================================"
    & $PythonExe ".\scripts\audit_master_phases.py" --categories
    $codes["audit"] = $LASTEXITCODE

    if (-not $SkipIntegration -and $codes["integration"] -eq 0 -and $codes["audit"] -eq 0) {
        $latestPointer = Get-Content -LiteralPath $pointerPath -Raw | ConvertFrom-Json
        $phase5Complete = @( $latestPointer.completed_phases | Where-Object { $_ -eq 5 } ).Count -gt 0
    }

    Write-Host ""
    Write-Host "========================================================================"
    Write-Host "RESUMEN EJECUCION FASE 5"
    Write-Host "========================================================================"
    foreach ($key in @("similares", "heb", "sams", "duero", "duero_packaging", "duero_storefront", "preview", "integration", "audit")) {
        Write-Host ("{0,-19}: {1}" -f $key, $codes[$key])
    }
    Write-Host "FASE 5                 : $(if ($SkipIntegration) { 'CAPTURA / SIN INTEGRAR' } elseif ($phase5Complete) { 'COMPLETE' } else { 'PARTIAL_VALIDATED / PENDING' })"
    Write-Host "DUERO ONLINE           : REQUIERE APROBACION EXPLICITA SI SOLO SE CERTIFICA CATALOGO PUBLICO"
    Write-Host "Log                    : $log"
} finally {
    try { Stop-Transcript | Out-Null } catch {}
}

# Exit code for -SkipIntegration must reflect capture quality, not phase closure.
# Fase 5 cannot become COMPLETE without integration, by design.
if ($SkipIntegration) {
    $captureOk = $true
    foreach ($key in @("similares", "heb", "sams", "duero", "duero_packaging", "duero_storefront", "preview", "audit")) {
        if ($codes[$key] -ne 0) { $captureOk = $false }
    }
    if ($captureOk) {
        Write-Host "Captura completa validada; integración omitida por solicitud."
        exit 0
    }
    Write-Warning "Captura Fase 5 con observaciones; el master no fue modificado por el integrador."
    exit 2
}
if ($phase5Complete -and $codes["audit"] -eq 0) { exit 0 }
Write-Warning "Fase 5 tiene pendientes u observaciones. Los retailers completos se conservan; consulta la auditoria."
exit 2
