param(
    [string]$Category = "descongestionantes",
    [int]$MaxPages = 20,
    [switch]$UpdateConsolidated
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = "C:\Proyectos\venvs\scraper\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

$ChromeProcesses = Get-Process chrome -ErrorAction SilentlyContinue
if (-not $ChromeProcesses) {
    Write-Host "ERROR: no hay ninguna sesion de Chrome abierta."
    exit 10
}

$ChromeUserData = Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data"
$ActivePortFiles = @(
    Get-ChildItem -Path $ChromeUserData -Filter "DevToolsActivePort" -File -Recurse -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending
)

if (-not $ActivePortFiles -or $ActivePortFiles.Count -eq 0) {
    Write-Host "ERROR: Chrome esta abierto pero remote debugging no esta habilitado."
    exit 11
}

$ActivePortPath = ($ActivePortFiles | Select-Object -First 1).FullName
$Lines = @(Get-Content -LiteralPath $ActivePortPath -ErrorAction Stop)
if ($Lines.Count -lt 2) {
    Write-Host "ERROR: DevToolsActivePort invalido."
    exit 12
}

$Port = $Lines[0].Trim()
$WebSocketPath = $Lines[1].Trim()
$WsUrl = "ws://127.0.0.1:$Port$WebSocketPath"

Write-Host "Chrome existente detectado."
Write-Host "Puerto    : $Port"
Write-Host "Retailer  : Farmacias San Pablo"
Write-Host "Categoria : $Category"
Write-Host "Modo      : asistido, CDP directo."
Write-Host ""

$ArgsList = @(
    ".\scripts\scrape_san_pablo_assisted_raw_cdp.py",
    "--category", $Category,
    "--max-pages", $MaxPages,
    "--ws-url", $WsUrl
)

if ($UpdateConsolidated) {
    $ArgsList += "--update-consolidated"
}

& $Python @ArgsList
exit $LASTEXITCODE
