param(
    [string]$ProjectDir = "",
    [string]$PythonExe = "",
    [string]$Category = "all",
    [int]$MaxPages = 20,
    [string]$CdpUrl = "",
    [switch]$SkipIntegration
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($ProjectDir)) {
    $ScriptFile = $MyInvocation.MyCommand.Path
    if ([string]::IsNullOrWhiteSpace($ScriptFile)) { throw "No se pudo determinar la ruta del proyecto. Especifica -ProjectDir." }
    $ProjectDir = Split-Path -Parent (Split-Path -Parent $ScriptFile)
}
Set-Location $ProjectDir

if ([string]::IsNullOrWhiteSpace($PythonExe) -and $env:VIRTUAL_ENV) {
    $candidate = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
    if (Test-Path $candidate) { $PythonExe = $candidate }
}

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $candidate = Join-Path $ProjectDir ".venv\Scripts\python.exe"
    if (Test-Path $candidate) { $PythonExe = $candidate }
}

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd) { $PythonExe = $cmd.Source }
}

if ([string]::IsNullOrWhiteSpace($PythonExe) -or -not (Test-Path $PythonExe)) {
    throw "No se encontro Python valido."
}

if ([string]::IsNullOrWhiteSpace($CdpUrl)) {
    $ChromeUserData = Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data"
    $ActivePortFiles = @()

    if (Test-Path $ChromeUserData) {
        $ActivePortFiles = @(Get-ChildItem -Path $ChromeUserData -Filter "DevToolsActivePort" -File -Recurse -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
    }

    if (-not $ActivePortFiles -or $ActivePortFiles.Count -eq 0) {
        Write-Host "============================================================"
        Write-Host "CHROME ABIERTO, PERO REMOTE DEBUGGING NO ESTA HABILITADO"
        Write-Host "============================================================"
        Write-Host "En ESA MISMA ventana de Chrome:"
        Write-Host "  1. Abre https://www.farmaciasanpablo.com.mx/"
        Write-Host "  2. Abre chrome://inspect/#remote-debugging"
        Write-Host "  3. Activa Allow remote debugging for this browser instance"
        Write-Host "  4. Acepta Allow si Chrome lo solicita"
        Write-Host "  5. Ejecuta nuevamente este comando"
        exit 11
    }

    $ActivePortPath = ($ActivePortFiles | Select-Object -First 1).FullName
    $Lines = @(Get-Content -LiteralPath $ActivePortPath -ErrorAction Stop)
    if ($Lines.Count -lt 1) {
        Write-Host "ERROR: DevToolsActivePort invalido: $ActivePortPath"
        exit 12
    }

    if ($Lines.Count -lt 2) {
        Write-Host "ERROR: DevToolsActivePort no contiene WebSocket browser path."
        exit 12
    }

    $Port = $Lines[0].Trim()
    $WebSocketPath = $Lines[1].Trim()
    $CdpUrl = "ws://127.0.0.1:$Port$WebSocketPath"
}

Write-Host "============================================================"
Write-Host "Farmacias San Pablo - Chrome existente"
Write-Host "============================================================"
Write-Host "Python   : $PythonExe"
Write-Host "Category : $Category"
Write-Host "MaxPages : $MaxPages"
Write-Host "Chrome   : SESION EXISTENTE"
Write-Host "CDP      : $CdpUrl"
Write-Host ""

& $PythonExe .\scripts\run_san_pablo_full.py --category $Category --max-pages $MaxPages --cdp-url $CdpUrl
$code = $LASTEXITCODE

Write-Host ""
Write-Host "Salida: $ProjectDir\output\farmacias_san_pablo_test.xlsx"
Write-Host "Consolidado: $ProjectDir\output\concentrado_scraper.xlsx"

if ($code -ne 0) {
    Write-Warning "El test termino con observaciones (exit_code=$code). Revisa la hoja Resumen; el archivo de salida se conserva."
}

if ($Category -eq "all" -and -not $SkipIntegration) {
    Write-Host ""
    Write-Host "Evaluando retailers completos de Fase 4; parciales quedan pendientes..."
    & ".\scripts\integrate_complete_retailers.ps1" `
        -Phase 4 `
        -ProjectDir $ProjectDir `
        -PythonExe $PythonExe

    $integrationCode = $LASTEXITCODE
    if ($integrationCode -ne 0) {
        Write-Warning "No se pudo actualizar el master de Fase 4."
        if ($code -eq 0) { $code = $integrationCode }
    }
}

if ($SkipIntegration) {
    Write-Host "Integracion al master: OMITIDA por -SkipIntegration."
}

exit $code
