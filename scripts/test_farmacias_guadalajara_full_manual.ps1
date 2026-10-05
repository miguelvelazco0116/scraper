param()

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
    Write-Host "ERROR: Chrome esta abierto, pero remote debugging no esta habilitado."
    Write-Host "Abre chrome://inspect/#remote-debugging en ESTA misma sesion y habilitalo."
    exit 11
}

$ActivePortFile = $ActivePortFiles | Select-Object -First 1
$ActivePortPath = $ActivePortFile.FullName
if (-not $ActivePortPath -or -not (Test-Path -LiteralPath $ActivePortPath)) {
    Write-Host "ERROR: no se pudo resolver DevToolsActivePort."
    exit 12
}

$Lines = @(Get-Content -LiteralPath $ActivePortPath -ErrorAction Stop)
if ($Lines.Count -lt 2) {
    Write-Host "ERROR: DevToolsActivePort invalido."
    exit 12
}

$Port = $Lines[0].Trim()
$WebSocketPath = $Lines[1].Trim()
$CdpUrl = "http://127.0.0.1:$Port"

Write-Host "Chrome existente detectado."
try {
    $Version = Invoke-RestMethod -Uri "$CdpUrl/json/version" -TimeoutSec 5
    Write-Host "Chrome CDP listo: $($Version.Browser)"
} catch {
    Write-Host "ERROR: Chrome expone DevToolsActivePort pero /json/version no responde."
    exit 13
}

Write-Host "CDP: $CdpUrl"
Write-Host "El test usara ESTA sesion; NO abrira otro navegador."
Write-Host ""

& $Python ".\scripts\test_farmacias_guadalajara_full_manual.py" --cdp-url $CdpUrl
exit $LASTEXITCODE
