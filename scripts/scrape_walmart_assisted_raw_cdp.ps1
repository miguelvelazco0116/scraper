param(
    [string]$Category = "cuidado-bucal"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = "C:\Proyectos\venvs\scraper\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

$ChromeProcesses = Get-Process chrome -ErrorAction SilentlyContinue
if (-not $ChromeProcesses) {
    Write-Host "ERROR: no hay ninguna sesión de Chrome abierta."
    exit 10
}

$ChromeUserData = Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data"
$ActivePortFiles = @(
    Get-ChildItem -Path $ChromeUserData -Filter "DevToolsActivePort" -File -Recurse -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending
)

if (-not $ActivePortFiles -or $ActivePortFiles.Count -eq 0) {
    Write-Host "ERROR: Chrome está abierto pero remote debugging no está habilitado."
    exit 11
}

$ActivePortPath = ($ActivePortFiles | Select-Object -First 1).FullName
$Lines = @(Get-Content -LiteralPath $ActivePortPath -ErrorAction Stop)
if ($Lines.Count -lt 2) {
    Write-Host "ERROR: DevToolsActivePort inválido."
    exit 12
}

$Port = $Lines[0].Trim()
$WebSocketPath = $Lines[1].Trim()
$WsUrl = "ws://127.0.0.1:$Port$WebSocketPath"

Write-Host "Chrome existente detectado."
Write-Host "Puerto    : $Port"
Write-Host "Retailer  : Walmart México"
Write-Host "Categoría : $Category"
Write-Host "Modo      : asistido, CDP directo."
Write-Host ""
Write-Host "IMPORTANTE: Walmart debe mostrar SC Toreo / CP 11220 antes de iniciar."
Write-Host ""

$ArgsList = @(
    ".\scripts\scrape_walmart_assisted_raw_cdp.py",
    "--category", $Category,
    "--ws-url", $WsUrl
)

& $Python @ArgsList
exit $LASTEXITCODE
