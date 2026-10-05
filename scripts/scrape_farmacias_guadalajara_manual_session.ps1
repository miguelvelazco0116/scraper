param(
    [string]$Category = "cuidado-bucal",
    [switch]$UpdateConsolidated
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = "C:\Proyectos\venvs\scraper\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

$ChromeProcesses = Get-Process chrome -ErrorAction SilentlyContinue
if (-not $ChromeProcesses) {
    Write-Host "ERROR: no hay ninguna sesion de Google Chrome abierta."
    Write-Host "Este script NO abrira Chrome."
    exit 10
}

$ChromeUserData = Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data"

$ActivePortFiles = @()
if (Test-Path $ChromeUserData) {
    $ActivePortFiles = @(Get-ChildItem -Path $ChromeUserData -Filter "DevToolsActivePort" -File -Recurse -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
}

if (-not $ActivePortFiles -or $ActivePortFiles.Count -eq 0) {
    Write-Host "ERROR: Chrome esta abierto, pero la depuracion remota no esta habilitada."
    Write-Host ""
    Write-Host "NO cierres Chrome."
    Write-Host "En la misma sesion abierta:"
    Write-Host "  1. Abre chrome://inspect/#remote-debugging"
    Write-Host "  2. Activa Allow remote debugging for this browser instance"
    Write-Host "  3. Si Chrome solicita permiso, selecciona Allow"
    Write-Host "  4. Regresa a Farmacias Guadalajara"
    Write-Host "  5. Ejecuta nuevamente este mismo comando"
    Write-Host ""
    Write-Host "El scraper NO abrira otra ventana."
    exit 11
}

$ActivePortFile = $ActivePortFiles | Select-Object -First 1
$ActivePortPath = $ActivePortFile.FullName

if (-not $ActivePortPath -or -not (Test-Path -LiteralPath $ActivePortPath)) {
    Write-Host "ERROR: se detecto DevToolsActivePort pero no fue posible resolver su ruta absoluta."
    Write-Host "Valor detectado: $ActivePortFile"
    exit 12
}

$Lines = @(Get-Content -LiteralPath $ActivePortPath -ErrorAction Stop)

if ($Lines.Count -lt 2) {
    Write-Host "ERROR: DevToolsActivePort existe pero no contiene un endpoint valido."
    Write-Host "Archivo: $ActivePortPath"
    exit 12
}

$Port = $Lines[0].Trim()
$WebSocketPath = $Lines[1].Trim()

if (-not $Port -or -not $WebSocketPath) {
    Write-Host "ERROR: DevToolsActivePort incompleto."
    exit 12
}

$CdpUrl = "ws://127.0.0.1:$Port$WebSocketPath"

Write-Host "Chrome existente detectado."
Write-Host "DevToolsActivePort : $ActivePortPath"
Write-Host "Puerto             : $Port"
Write-Host "El scraper se conectara a ESTA sesion; NO abrira otro navegador."
Write-Host ""

$argsList = @(
    ".\scripts\scrape_farmacias_guadalajara_manual_session.py",
    "--category", $Category,
    "--cdp-url", $CdpUrl
)

if ($UpdateConsolidated) {
    $argsList += "--update-consolidated"
}

& $Python @argsList
exit $LASTEXITCODE
