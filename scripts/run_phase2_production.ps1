param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [string]$CdpUrl = ""
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectDir

$LogDir = Join-Path $ProjectDir "diagnostics\phase2_production"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogPath = Join-Path $LogDir "phase2_$LogStamp.log"
Start-Transcript -Path $LogPath -Force | Out-Null

if ([string]::IsNullOrWhiteSpace($PythonExe) -and $env:VIRTUAL_ENV) {
    $candidate = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
    if (Test-Path $candidate) { $PythonExe = $candidate }
}

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $candidate = "C:\Proyectos\venvs\scraper\Scripts\python.exe"
    if (Test-Path $candidate) { $PythonExe = $candidate }
}

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd) { $PythonExe = $cmd.Source }
}

if ([string]::IsNullOrWhiteSpace($PythonExe) -or -not (Test-Path $PythonExe)) {
    throw "No se encontro Python valido para ejecutar la Fase 2."
}

$ChromeProcesses = Get-Process chrome -ErrorAction SilentlyContinue
if (-not $ChromeProcesses) {
    Write-Host "ERROR: abre Google Chrome manualmente."
    Write-Host "Debes dejar abiertas dos pestanas:"
    Write-Host "  1. https://www.farmaciasguadalajara.com/"
    Write-Host "  2. https://www.soriana.com/"
    try { Stop-Transcript | Out-Null } catch {}
    exit 10
}

if ([string]::IsNullOrWhiteSpace($CdpUrl)) {
    $ChromeUserData = Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data"
    $ActivePortFiles = @()
    if (Test-Path $ChromeUserData) {
        $ActivePortFiles = @(Get-ChildItem -Path $ChromeUserData -Filter "DevToolsActivePort" -File -Recurse -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
    }

    if (-not $ActivePortFiles -or $ActivePortFiles.Count -eq 0) {
        Write-Host "========================================================================"
        Write-Host "CHROME ABIERTO, PERO REMOTE DEBUGGING NO ESTA HABILITADO"
        Write-Host "========================================================================"
        Write-Host "En ESA MISMA ventana de Chrome:"
        Write-Host "  1. Abre chrome://inspect/#remote-debugging"
        Write-Host "  2. Activa Allow remote debugging for this browser instance"
        Write-Host "  3. Acepta Allow si Chrome lo solicita"
        Write-Host "  4. Deja abiertas una pestana de Guadalajara y otra de Soriana"
        Write-Host "  5. Ejecuta nuevamente este comando"
        try { Stop-Transcript | Out-Null } catch {}
        exit 11
    }

    $ActivePortPath = ($ActivePortFiles | Select-Object -First 1).FullName
    $Lines = @(Get-Content -LiteralPath $ActivePortPath -ErrorAction Stop)
    if ($Lines.Count -lt 2) {
        Write-Host "ERROR: DevToolsActivePort invalido: $ActivePortPath"
        try { Stop-Transcript | Out-Null } catch {}
        exit 12
    }

    $Port = $Lines[0].Trim()
    $WebSocketPath = $Lines[1].Trim()
    $CdpUrl = "ws://127.0.0.1:$Port$WebSocketPath"
}

Write-Host "========================================================================"
Write-Host "SCRAPER PRODUCTIVO - FASE 2"
Write-Host "========================================================================"
Write-Host "Retailers : Farmacias Guadalajara + Soriana"
Write-Host "Chrome    : SESION EXISTENTE"
Write-Host "CDP       : $CdpUrl"
Write-Host "Salida    : $ProjectDir\output\concentrado_fase2.xlsx"
Write-Host "Baseline  : $ProjectDir\output\fase2_muestra_inicial_valida.xlsx"
Write-Host "Log       : $LogPath"
Write-Host ""
Write-Host "IMPORTANTE:"
Write-Host "- Deja una pestana abierta de Farmacias Guadalajara."
Write-Host "- Deja otra pestana abierta de Soriana."
Write-Host "- El runner reutiliza los metodos ya validados."
Write-Host "- El master solo se reemplaza si TODAS las categorias pasan calidad."
Write-Host ""

$ArgsList = @(".\scripts\run_phase2_production.py", "--cdp-url", $CdpUrl)
& $PythonExe @ArgsList
$code = $LASTEXITCODE

Write-Host ""
if ($code -eq 0) {
    Write-Host "========================================================================"
    Write-Host "FASE 2 PRODUCTIVA PUBLICADA"
    Write-Host "========================================================================"
    Write-Host "Archivo : $ProjectDir\output\concentrado_fase2.xlsx"
    Write-Host "Baseline: $ProjectDir\output\fase2_muestra_inicial_valida.xlsx"
    Write-Host "Log     : $LogPath"
    Write-Host ""
    Write-Host "Fase 2 valida. Integrando automaticamente al concentrado diario..."

    & ".\scripts\integrate_validated_phase.ps1" `
        -Phase 2 `
        -ProjectDir $ProjectDir `
        -PythonExe $PythonExe

    $integrationCode = $LASTEXITCODE
    if ($integrationCode -ne 0) {
        Write-Warning "Fase 2 fue publicada, pero NO pudo integrarse al concentrado diario."
        $code = $integrationCode
    } else {
        Write-Host "FASE 2 VALIDADA E INTEGRADA AL CONCENTRADO."
    }
} else {
    Write-Warning "La Fase 2 NO fue publicada. El master productivo anterior se conserva."
    Write-Host "Diagnostico: $ProjectDir\diagnostics\phase2_production\last_run.json"
    Write-Host "Log        : $LogPath"
}

try { Stop-Transcript | Out-Null } catch {}
exit $code
