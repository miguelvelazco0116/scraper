param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [string]$CdpUrl = "",
    [switch]$SkipIntegration
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectDir

$LogDir = Join-Path $ProjectDir "diagnostics\phase3_production"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogPath = Join-Path $LogDir "phase3_$Stamp.log"
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
    throw "No se encontro Python valido para ejecutar la Fase 3."
}

$ChromeProcesses = Get-Process chrome -ErrorAction SilentlyContinue
if (-not $ChromeProcesses) {
    Write-Host "ERROR: abre Google Chrome manualmente."
    Write-Host "Debes dejar abiertas dos pestanas:"
    Write-Host "  1. https://www.bodegaaurrera.com.mx/"
    Write-Host "  2. https://www.walmart.com.mx/"
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
        Write-Host "  4. Deja abiertas Bodega Aurrera y Walmart"
        Write-Host "  5. En Walmart confirma SC Toreo / CP 11220"
        Write-Host "  6. Ejecuta nuevamente este comando"
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
Write-Host "SCRAPER PRODUCTIVO - FASE 3"
Write-Host "========================================================================"
Write-Host "Retailers : Bodega Aurrera + Walmart"
Write-Host "Categorias: 3 + 3 = 6"
Write-Host "Python    : $PythonExe"
Write-Host "Chrome    : SESION EXISTENTE"
Write-Host "CDP       : $CdpUrl"
Write-Host "Walmart   : SC Toreo (2344) / CP 11220"
Write-Host "Salida    : $ProjectDir\output\concentrado_fase3.xlsx"
Write-Host "Baseline  : $ProjectDir\output\fase3_muestra_inicial_valida.xlsx"
Write-Host "Log       : $LogPath"
Write-Host ""
Write-Host "IMPORTANTE:"
Write-Host "- Deja una pestana abierta de Bodega Aurrera."
Write-Host "- Deja otra pestana abierta de Walmart."
Write-Host "- En Walmart deja seleccionada SC Toreo / CP 11220."
Write-Host "- El runner NO abre ni cierra Chrome."
Write-Host "- Bodega y Walmart se ejecutan secuencialmente."
Write-Host "- La Fase 3 completa solo se publica si las 5 categorias pasan calidad."
Write-Host "- El master integra retailers completos incluso si otro queda parcial."
Write-Host ""

$ArgsList = @(
    ".\scripts\run_phase3_production.py",
    "--cdp-url", $CdpUrl
)

& $PythonExe @ArgsList
$code = $LASTEXITCODE

Write-Host ""
if ($code -eq 0) {
    Write-Host "========================================================================"
    Write-Host "FASE 3 PRODUCTIVA PUBLICADA"
    Write-Host "========================================================================"
    Write-Host "Archivo : $ProjectDir\output\concentrado_fase3.xlsx"
    Write-Host "Baseline: $ProjectDir\output\fase3_muestra_inicial_valida.xlsx"
    Write-Host "Log     : $LogPath"
} else {
    Write-Warning "La Fase 3 NO fue publicada."
    Write-Host "Los retailers incompletos no modifican el master."
    Write-Host "Diagnostico: $ProjectDir\diagnostics\phase3_production\last_run.json"
    Write-Host "Log        : $LogPath"
}

if (-not $SkipIntegration) {
    Write-Host ""
    Write-Host "INTEGRACION POR RETAILER: solo COMPLETE; PARTIAL queda pendiente."
    & ".\scripts\integrate_complete_retailers.ps1" `
        -Phase 3 `
        -ProjectDir $ProjectDir `
        -PythonExe $PythonExe
    $integrationCode = $LASTEXITCODE
    if ($integrationCode -ne 0) {
        Write-Warning "No fue posible integrar retailers completos de Fase 3."
        if ($code -eq 0) { $code = $integrationCode }
    }
} else {
    Write-Host "Integracion omitida por -SkipIntegration."
}

try { Stop-Transcript | Out-Null } catch {}
exit $code
