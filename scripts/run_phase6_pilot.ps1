param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [ValidateSet("probe","capture")][string]$Mode = "probe",
    [ValidateSet("all","farmacias-yza","farmatodo")][string]$Retailer = "all",
    [string]$CdpUrl = "",
    [switch]$UseExistingChrome,
    [ValidateRange(1, 200)][int]$MaxPages = 100
)
$ErrorActionPreference = "Stop"
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
    throw "Activa el entorno virtual C:\Proyectos\venvs\scraper antes de continuar."
}
# YZA follows HEB: operator opens Chrome first and enables remote debugging.
# Chrome M144+ chrome://inspect remote debugging may return HTTP 404 for
# /json/version and /json/list. Read the ACTUAL browser websocket address
# from DevToolsActivePort instead of rejecting a valid debugging session.
$RequiresYza = ($Retailer -eq "farmacias-yza" -or $Retailer -eq "all")

if ($RequiresYza) {
    if (-not (Get-Process chrome -ErrorAction SilentlyContinue)) {
        throw "Abre Google Chrome manualmente, habilita depuracion remota desde chrome://inspect/#remote-debugging y prepara YZA CP 11590."
    }

    $BrowserCandidates = @()
    if ($CdpUrl) {
        $BrowserCandidates = @($CdpUrl)
    } else {
        $Roots = @(
            (Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data"),
            (Join-Path $env:LOCALAPPDATA "ScraperYZAChrome")
        )
        $PortFiles = @()
        foreach ($Root in $Roots) {
            if (Test-Path -LiteralPath $Root) {
                $PortFiles += @(
                    Get-ChildItem -LiteralPath $Root -Filter "DevToolsActivePort" -File -Recurse -ErrorAction SilentlyContinue
                )
            }
        }

        foreach ($PortFile in @($PortFiles | Sort-Object LastWriteTime -Descending)) {
            $Lines = @(Get-Content -LiteralPath $PortFile.FullName -ErrorAction SilentlyContinue)
            if ($Lines.Count -lt 2) { continue }
            $PortString = $Lines[0].Trim()
            $BrowserPath = $Lines[1].Trim()

            # Second line is the browser target WebSocket path. A port alone
            # is not enough for the new Chrome on-demand debugging server.
            if ($PortString -notmatch '^\d{2,5}$' -or
                [int]$PortString -gt 65535 -or
                $BrowserPath -notmatch '^/devtools/browser/[A-Za-z0-9_-]+$') {
                continue
            }
            $BrowserCandidates += "ws://127.0.0.1:$PortString$BrowserPath"
        }

        # Legacy Chrome started with explicit --remote-debugging-port=9222.
        # Only use HTTP discovery when it actually returns browser metadata.
        try {
            $Version = Invoke-RestMethod -Uri "http://127.0.0.1:9222/json/version" -TimeoutSec 2 -ErrorAction Stop
            if ($Version.webSocketDebuggerUrl) {
                $BrowserCandidates += [string]$Version.webSocketDebuggerUrl
            }
        } catch { }

        $BrowserCandidates = @($BrowserCandidates | Select-Object -Unique)
    }

    $CdpUrl = ""
    foreach ($Candidate in $BrowserCandidates) {
        try {
            $Endpoint = [uri]$Candidate
            if ($Endpoint.Host -notin @("127.0.0.1", "localhost") -or
                $Endpoint.Port -lt 1 -or
                $Endpoint.Port -gt 65535 -or
                $Endpoint.Scheme -notin @("ws", "http")) {
                continue
            }
            if ($Endpoint.Scheme -eq "ws" -and
                $Endpoint.AbsolutePath -notmatch '^/devtools/browser/[A-Za-z0-9_-]+$') {
                continue
            }

            # Unlike /json/version, TCP works for Chrome inspect WebSockets.
            $Socket = New-Object System.Net.Sockets.TcpClient
            try {
                $Pending = $Socket.BeginConnect("127.0.0.1", $Endpoint.Port, $null, $null)
                if (-not $Pending.AsyncWaitHandle.WaitOne(2000)) { continue }
                $Socket.EndConnect($Pending)
                $CdpUrl = $Candidate
                break
            } finally {
                $Socket.Dispose()
            }
        } catch { }
    }

    if (-not $CdpUrl) {
        throw (
            "Chrome esta abierto, pero no se encontro depuracion remota activa. " +
            "En ESA MISMA ventana abre chrome://inspect/#remote-debugging; " +
            "activa 'Allow remote debugging for this browser instance'. " +
            "Comprueba que Chrome muestre 'Server running at:' y deja abierta " +
            "la pestana YZA con CP 11590."
        )
    }

    Write-Host "Chrome remoto detectado: endpoint WebSocket local disponible."
    Write-Host "El scraper buscara la pestana www.yza.mx al conectarse."
    Write-Host "Si Chrome solicita permiso para controlar esta sesion, pulsa Allow."
    Write-Host "El scraper no cambia el CP ni cierra tu Chrome."
} elseif ($UseExistingChrome -and -not $CdpUrl) {
    throw "Farmatodo no requiere Chrome manual. Omite -UseExistingChrome."
}
Write-Host "FASE 6 | FARMACIAS YZA (CP 11590) + FARMATODO (SIN CP)"
Write-Host "Modo: $Mode | Retailer: $Retailer | MaxPages: $MaxPages"
if ($CdpUrl) {
    Write-Host "Navegador: Chrome preparado manualmente por operador (CDP)"
} else {
    Write-Host "Navegador: Chrome automatico visible (solo Farmatodo)"
}
Write-Host "Master Fases 1-5: SIN CAMBIOS"
$argsList = @(".\scripts\run_phase6_pilot.py", "--mode", $Mode, "--retailer", $Retailer, "--max-pages", [string]$MaxPages)
if ($CdpUrl) { $argsList += @("--cdp-url", $CdpUrl) }
& $PythonExe @argsList
exit $LASTEXITCODE
