param(
    [string]$ProjectDir = "",
    [string]$PythonExe = "",
    [string]$CdpUrl = "",
    [string]$Category = "",
    [ValidateRange(0, 5)]
    [int]$RetryFailedAtEnd = 1
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

if ([string]::IsNullOrWhiteSpace($CdpUrl)) {
    $ChromeUserData = Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data"
    $ActivePortFiles = @()
    if (Test-Path $ChromeUserData) {
        $ActivePortFiles = @(Get-ChildItem -Path $ChromeUserData -Filter "DevToolsActivePort" -File -Recurse -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
    }
    if (-not $ActivePortFiles -or $ActivePortFiles.Count -eq 0) {
        Write-Host "ERROR: habilita remote debugging en el Chrome ya abierto."
        Write-Host "Abre chrome://inspect/#remote-debugging y activa Allow remote debugging for this browser instance."
        exit 11
    }
    $ActivePortPath = ($ActivePortFiles | Select-Object -First 1).FullName
    $Lines = @(Get-Content -LiteralPath $ActivePortPath -ErrorAction Stop)
    if ($Lines.Count -lt 2) { exit 12 }
    $Port = $Lines[0].Trim()
    $WebSocketPath = $Lines[1].Trim()
    $CdpUrl = "ws://127.0.0.1:$Port$WebSocketPath"
}

Write-Host "============================================================"
Write-Host "Bodega Aurrera - Chrome existente"
Write-Host "============================================================"
Write-Host "Python: $PythonExe"
Write-Host "Chrome: SESION EXISTENTE"
Write-Host "CDP   : $CdpUrl"
Write-Host ""

$ArgsList = @(
    ".\scripts\test_bodega_aurrera.py",
    "--cdp-url", $CdpUrl,
    "--retry-failed-at-end", "$RetryFailedAtEnd"
)
if (-not [string]::IsNullOrWhiteSpace($Category)) {
    $ArgsList += @("--category", $Category)
    Write-Host "Category: $Category"
}
& $PythonExe @ArgsList
$code = $LASTEXITCODE

Write-Host "Salida: $ProjectDir\output\bodega_aurrera_test.xlsx"

if ($code -eq 0 -and [string]::IsNullOrWhiteSpace($Category)) {
    Write-Host ""
    Write-Host "Bodega Aurrera completa y valida."
    Write-Host "Agregando la particion validada al concentrado diario..."

    & ".\scripts\integrate_validated_phase.ps1" `
        -Phase 3 `
        -Retailer "Bodega Aurrera" `
        -ProjectDir $ProjectDir `
        -PythonExe $PythonExe

    $integrationCode = $LASTEXITCODE
    if ($integrationCode -ne 0) {
        Write-Warning "Bodega fue valida, pero NO pudo integrarse al concentrado diario."
        $code = $integrationCode
    } else {
        Write-Host "BODEGA AURRERA VALIDADA E INTEGRADA AL CONCENTRADO."
    }
} elseif ($code -ne 0) {
    Write-Warning "El test termino con observaciones (exit_code=$code)."
}

exit $code
