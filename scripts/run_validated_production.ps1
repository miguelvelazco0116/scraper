param(
    [string]$ProjectDir = "",
    [string]$PythonExe = "",
    [string]$ProfileDir = ".chedraui_profile",
    [string]$CdpUrl = "",
    [switch]$StandardMemory
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($ProjectDir)) {
    $ScriptFile = $MyInvocation.MyCommand.Path
    if ([string]::IsNullOrWhiteSpace($ScriptFile)) { throw "No se pudo determinar la ruta del proyecto. Especifica -ProjectDir." }
    $ProjectDir = Split-Path -Parent (Split-Path -Parent $ScriptFile)
}
Set-Location $ProjectDir

$LogDir = Join-Path $ProjectDir "diagnostics\production_master"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogPath = Join-Path $LogDir "production_$LogStamp.log"
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
    throw "No se encontro Python valido para ejecutar la corrida productiva integral."
}

$ChromeProcesses = Get-Process chrome -ErrorAction SilentlyContinue
if (-not $ChromeProcesses) {
    Write-Host "ERROR: abre Google Chrome manualmente para la Fase 2."
    Write-Host "Deja abiertas estas pestanas:"
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
        Write-Host "En esa misma ventana de Chrome:"
        Write-Host "  1. Abre chrome://inspect/#remote-debugging"
        Write-Host "  2. Activa Allow remote debugging for this browser instance"
        Write-Host "  3. Acepta Allow si Chrome lo solicita"
        Write-Host "  4. Deja abiertas Guadalajara y Soriana"
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
Write-Host "SCRAPER PRODUCTIVO INTEGRAL - FASE 1 + FASE 2"
Write-Host "========================================================================"
Write-Host "Python    : $PythonExe"
Write-Host "Profile   : $ProfileDir"
Write-Host "Memoria   : $(if ($StandardMemory) { 'STANDARD' } else { 'LOW-MEMORY' })"
Write-Host "Chrome    : SESION EXISTENTE PARA FASE 2"
Write-Host "CDP       : $CdpUrl"
Write-Host "Master    : $ProjectDir\output\concentrado_productivo_total.xlsx"
Write-Host "Historico : $ProjectDir\output\extractos_validos"
Write-Host "Log       : $LogPath"
Write-Host ""
Write-Host "La corrida ejecuta primero Fase 1 y despues Fase 2."
Write-Host "Solo si ambas pasan se publica el master y un extracto historico inmutable."
Write-Host ""

$ArgsList = @(
    ".\scripts\run_validated_production.py",
    "--cdp-url", $CdpUrl,
    "--profile-dir", $ProfileDir
)

if ($StandardMemory) {
    $ArgsList += "--standard-memory"
}

& $PythonExe @ArgsList
$code = $LASTEXITCODE

Write-Host ""
if ($code -eq 0) {
    Write-Host "========================================================================"
    Write-Host "CORRIDA PRODUCTIVA INTEGRAL PUBLICADA"
    Write-Host "========================================================================"
    Write-Host "Master    : $ProjectDir\output\concentrado_productivo_total.xlsx"
    Write-Host "Baseline  : $ProjectDir\output\muestra_inicial_valida_total.xlsx"
    Write-Host "Historico : $ProjectDir\output\extractos_validos"
    Write-Host "Manifest  : $ProjectDir\output\extractos_validos\manifest.csv"
    Write-Host "Log       : $LogPath"
} else {
    Write-Warning "La corrida integral NO fue publicada."
    Write-Host "El master general y los extractos historicos anteriores se conservan."
    Write-Host "Diagnostico: $ProjectDir\diagnostics\production_master\last_run.json"
    Write-Host "Log        : $LogPath"
}

try { Stop-Transcript | Out-Null } catch {}
exit $code
