param(
    [string]$Category = "cuidado-bucal",
    [switch]$UpdateConsolidated,
    [int]$RemoteDebuggingPort = 9223
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

$CdpUrl = "http://127.0.0.1:$RemoteDebuggingPort"
try {
    $null = Invoke-RestMethod -Uri "$CdpUrl/json/version" -TimeoutSec 3
} catch {
    Write-Host "ERROR: Chrome esta abierto, pero esa sesion no permite conexion CDP en $CdpUrl."
    Write-Host "El scraper no abrira otra ventana ni reiniciara tu navegador."
    Write-Host "Para poder controlar una sesion ya abierta, Chrome debe haberse iniciado con remote debugging."
    exit 11
}

Write-Host "Chrome existente detectado."
Write-Host "CDP disponible: $CdpUrl"
Write-Host "El scraper se conectara a esta sesion; NO abrira otro navegador."
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
