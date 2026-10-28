param(
    [string]$ProjectDir = "",
    [string]$PythonExe = "",
    [ValidateSet("all","83420","44220","54030","97245")][string]$PostalCode = "all",
    [string]$Category = "cuidado-bucal",
    [string]$CdpUrl = "",
    [ValidateRange(1,200)][int]$MaxPages = 100,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
# PowerShell script variables are not guaranteed during param default evaluation.
if ([string]::IsNullOrWhiteSpace($ProjectDir)) {
    $ScriptFile = $MyInvocation.MyCommand.Path
    if ([string]::IsNullOrWhiteSpace($ScriptFile)) {
        throw "No se pudo determinar la ruta del proyecto. Especifica -ProjectDir."
    }
    $ProjectDir = Split-Path -Parent (Split-Path -Parent $ScriptFile)
}
Set-Location $ProjectDir

if (-not $PythonExe -and $env:VIRTUAL_ENV) {
    $candidate = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
    if (Test-Path $candidate) { $PythonExe = $candidate }
}
if (-not $PythonExe) {
    $candidate = "C:\Proyectos\venvs\scraper\Scripts\python.exe"
    if (Test-Path $candidate) { $PythonExe = $candidate }
}
if (-not $PythonExe -or -not (Test-Path $PythonExe)) {
    throw "Activa el entorno virtual scraper antes de iniciar."
}

# Match Chedraui: inspect SAME-DAY cases before starting a browser.
if ($DryRun) {
    Write-Host "SORIANA - SOLO INVENTARIO, sin abrir Chrome ni modificar el master"
    & $PythonExe ".\\scripts\\test_soriana_cp_rotation.py" --cp $PostalCode --category $Category --max-pages $MaxPages --dry-run
    exit $LASTEXITCODE
}

# Match YZA: Chrome is started and remote debugging enabled manually.
# Do not silently launch or switch the operator's browser.
if (-not (Get-Process -Name chrome -ErrorAction SilentlyContinue)) {
    Write-Warning "Google Chrome no se detecto. No se ha iniciado ningun scraping."
    Write-Host "1) Abre Google Chrome en Windows y entra a https://www.soriana.com/"
    Write-Host "2) En OTRA pestana de ese Chrome abre chrome://inspect/#remote-debugging"
    Write-Host "3) Activa Allow remote debugging for this browser instance."
    Write-Host "4) Manten ambas pestanas abiertas y reintenta este comando."
    exit 11
}

if (-not $CdpUrl) {
    $roots = @(
        (Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data"),
        (Join-Path $env:LOCALAPPDATA "ScraperSorianaChrome")
    )
    $ports = @()
    foreach ($root in $roots) {
        if (Test-Path -LiteralPath $root) {
            $ports += @(Get-ChildItem -LiteralPath $root -Filter "DevToolsActivePort" -File -Recurse -ErrorAction SilentlyContinue)
        }
    }
    foreach ($file in @($ports | Sort-Object LastWriteTime -Descending)) {
        $parts = @(Get-Content -LiteralPath $file.FullName -ErrorAction SilentlyContinue)
        if ($parts.Count -lt 2) { continue }
        $portText = $parts[0].Trim()
        $browserPath = $parts[1].Trim()
        if ($portText -notmatch '^\d{2,5}$' -or [int]$portText -gt 65535 -or
            $browserPath -notmatch '^/devtools/browser/[A-Za-z0-9_-]+$') {
            continue
        }
        $socket = New-Object System.Net.Sockets.TcpClient
        try {
            $connect = $socket.BeginConnect("127.0.0.1", [int]$portText, $null, $null)
            if (-not $connect.AsyncWaitHandle.WaitOne(1500)) { continue }
            $socket.EndConnect($connect)
            $CdpUrl = "ws://127.0.0.1:$portText$browserPath"
            break
        } catch {
            continue
        } finally {
            $socket.Dispose()
        }
    }
    # Compatibility with existing manually launched Chrome CDP port.
    if (-not $CdpUrl) {
        try {
            $version = Invoke-RestMethod -Uri "http://127.0.0.1:9222/json/version" -TimeoutSec 2 -ErrorAction Stop
            if ($version.webSocketDebuggerUrl) { $CdpUrl = [string]$version.webSocketDebuggerUrl }
        } catch { }
    }
}
if (-not $CdpUrl) {
    Write-Warning "Chrome esta abierto pero la depuracion remota local no esta activa."
    Write-Host "En esa ventana abre chrome://inspect/#remote-debugging y activa Allow."
    Write-Host "Confirma que se muestre Server running at: y deja Soriana abierta."
    exit 11
}
try {
    $endpoint = [uri]$CdpUrl
    if ($endpoint.Host -notin @("127.0.0.1", "localhost") -or
        $endpoint.Port -lt 1 -or $endpoint.Port -gt 65535 -or
        $endpoint.Scheme -notin @("ws", "http") -or
        ($endpoint.Scheme -eq "ws" -and $endpoint.AbsolutePath -notmatch '^/devtools/browser/[A-Za-z0-9_-]+$')) {
        throw "El endpoint de Chrome debe ser local, de tipo HTTP o browser WebSocket."
    }
} catch {
    throw "Endpoint CDP no valido para este piloto: $_"
}

Write-Host "SORIANA - PILOTO DE ROTACION DE CODIGOS POSTALES"
Write-Host "Rotacion : 83420 => 44220 => 54030 => 97245"
Write-Host "Seleccion: $PostalCode | Categoria: $Category"
Write-Host "Chrome CDP local: detectado"
Write-Host "Salida   : output\pilots\soriana_cp_rotation\"
Write-Host "Avance   : diagnostics\soriana_cp_rotation\progress_<dia>_<scope>.json"
Write-Host "Master productivo: SIN CAMBIOS"
& $PythonExe ".\scripts\test_soriana_cp_rotation.py" --cdp-url $CdpUrl --cp $PostalCode --category $Category --max-pages $MaxPages
exit $LASTEXITCODE
