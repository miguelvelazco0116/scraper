param(
    [string]$Category = "all",
    [string]$CdpUrl = "",
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectDir

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
    throw "No se encontro Python valido."
}

$ChromeProcesses = Get-Process chrome -ErrorAction SilentlyContinue
if (-not $ChromeProcesses) {
    Write-Host "ERROR: abre Google Chrome manualmente y entra a https://www.soriana.com/"
    exit 10
}

if ([string]::IsNullOrWhiteSpace($CdpUrl)) {
    $ChromeUserData = Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data"
    $ActivePortFiles = @()
    if (Test-Path $ChromeUserData) {
        $ActivePortFiles = @(
            Get-ChildItem -Path $ChromeUserData -Filter "DevToolsActivePort" -File -Recurse -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending
        )
    }

    if (-not $ActivePortFiles -or $ActivePortFiles.Count -eq 0) {
        Write-Host "========================================================================"
        Write-Host "CHROME ABIERTO, PERO REMOTE DEBUGGING NO ESTA HABILITADO"
        Write-Host "========================================================================"
        Write-Host "En ESA MISMA ventana de Chrome:"
        Write-Host "  1. Abre chrome://inspect/#remote-debugging"
        Write-Host "  2. Activa Allow remote debugging for this browser instance"
        Write-Host "  3. Acepta Allow si Chrome lo solicita"
        Write-Host "  4. Abre https://www.soriana.com/ en otra pestaña"
        Write-Host "  5. Ejecuta nuevamente este comando"
        Write-Host ""
        Write-Host "El scraper NO abrira un segundo Chrome."
        exit 11
    }

    $ActivePortPath = ($ActivePortFiles | Select-Object -First 1).FullName
    $Lines = @(Get-Content -LiteralPath $ActivePortPath -ErrorAction Stop)
    if ($Lines.Count -lt 2) {
        Write-Host "ERROR: DevToolsActivePort invalido: $ActivePortPath"
        exit 12
    }

    $Port = $Lines[0].Trim()
    $WebSocketPath = $Lines[1].Trim()
    $CdpUrl = "ws://127.0.0.1:$Port$WebSocketPath"
}

Write-Host "========================================================================"
Write-Host "FASE 2 - TEST SORIANA MANUAL"
Write-Host "========================================================================"
Write-Host "Category : $Category"
Write-Host "Chrome   : SESION EXISTENTE"
Write-Host "CDP      : $CdpUrl"
Write-Host ""

$ArgsList = @(
    ".\scripts\test_soriana_manual_phase2.py",
    "--category", $Category,
    "--cdp-url", $CdpUrl
)

& $PythonExe @ArgsList
exit $LASTEXITCODE
