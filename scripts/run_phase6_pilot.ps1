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
# YZA: operator opens Chrome first, as in scripts/test_heb.ps1.
# Auto-discover the *actual* CDP port instead of assuming port 9222.
# Farmatodo keeps Playwright-owned Chrome when run separately.
$RequiresYza = ($Retailer -eq "farmacias-yza" -or $Retailer -eq "all")
if ($RequiresYza) {
    if (-not (Get-Process chrome -ErrorAction SilentlyContinue)) {
        throw "Abre Google Chrome MANUALMENTE, visita www.yza.mx, configura CP 11590 y habilita chrome://inspect/#remote-debugging."
    }

    $BrowserCandidates = @()
    if (-not $CdpUrl) {
        $ChromeRoots = @(
            (Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data"),
            (Join-Path $env:LOCALAPPDATA "ScraperYZAChrome")
        )
        $PortFiles = @()
        foreach ($Root in $ChromeRoots) {
            if (Test-Path -LiteralPath $Root) {
                $PortFiles += @(Get-ChildItem -LiteralPath $Root -Filter "DevToolsActivePort" -File -Recurse -ErrorAction SilentlyContinue)
            }
        }
        foreach ($PortFile in @($PortFiles | Sort-Object LastWriteTime -Descending)) {
            $PortLines = @(Get-Content -LiteralPath $PortFile.FullName -ErrorAction SilentlyContinue)
            if ($PortLines.Count -ge 2 -and $PortLines[0].Trim() -match '^\d{2,5}$') {
                $BrowserCandidates += ("http://127.0.0.1:" + $PortLines[0].Trim())
            }
        }
        # Fallback only when it really responds; 9222 is NOT assumed active.
        $BrowserCandidates += "http://127.0.0.1:9222"
        $BrowserCandidates = @($BrowserCandidates | Select-Object -Unique)
    } else {
        $BrowserCandidates = @($CdpUrl)
    }

    $MatchingChrome = $null
    $RespondingChrome = $false
    foreach ($Candidate in $BrowserCandidates) {
        try {
            $Endpoint = [uri]$Candidate
            if ($Endpoint.Host -notin @("127.0.0.1", "localhost") -or
                $Endpoint.Scheme -notin @("http", "ws") -or
                $Endpoint.Port -lt 1) {
                continue
            }
            $Base = "http://127.0.0.1:$($Endpoint.Port)"
            $Version = Invoke-RestMethod -Uri "$Base/json/version" -TimeoutSec 3 -ErrorAction Stop
            if (-not $Version.webSocketDebuggerUrl) { continue }
            $RespondingChrome = $true
            $Tabs = @(Invoke-RestMethod -Uri "$Base/json/list" -TimeoutSec 3 -ErrorAction Stop)
            $YzaTabs = @($Tabs | Where-Object {
                $TabUrl = [string]$_.url
                $TabUrl -match '^https?://(www\.)?yza\.mx(?:/|$|[?#])'
            })
            if ($YzaTabs.Count -gt 0) {
                $MatchingChrome = $Base
                break
            }
        } catch {
            # Ignore stale DevToolsActivePort files and inactive old ports.
        }
    }

    if (-not $MatchingChrome) {
        if ($RespondingChrome) {
            throw "Chrome CDP responde, pero no hay pestaña de www.yza.mx en esa misma instancia. Abre YZA alli y selecciona CP 11590."
        }
        throw ("Chrome abierto, pero ninguna conexion CDP local responde. " +
            "En ESA MISMA ventana abre chrome://inspect/#remote-debugging, " +
            "activa 'Allow remote debugging for this browser instance', " +
            "acepta el permiso y deja abierta una pestaña www.yza.mx. " +
            "No necesitas adivinar el puerto ni cerrar Chrome.")
    }
    $CdpUrl = $MatchingChrome
    Write-Host "Chrome YZA encontrado: $CdpUrl"
    Write-Host "Pestana YZA detectada en la sesion manual."
    Write-Host "No se abrira ni cerrara Chrome; no se cambiara el CP."
} elseif ($UseExistingChrome -and -not $CdpUrl) {
    throw "Farmatodo no necesita Chrome manual; omite -UseExistingChrome o indica -CdpUrl."
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
