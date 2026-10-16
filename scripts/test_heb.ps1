param(
    [string]$Category = "all",
    [int]$RetryFailedAtEnd = 1,
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
    Write-Host "ERROR: abre Google Chrome manualmente."
    Write-Host "Abre esta categoria en una pestana:"
    Write-Host "https://www.heb.com.mx/cuidado-personal-y-belleza/higiene-dental/pasta-dental"
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
        Write-Host "  4. Abre la categoria Pasta Dental de HEB en otra pestana"
        Write-Host "  5. Verifica manualmente que el catalogo se vea normal"
        Write-Host "  6. Ejecuta nuevamente este comando"
        Write-Host ""
        Write-Host "El scraper NO abrira ni cerrara otro Chrome."
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

Write-Host "============================================================"
Write-Host "FASE 5 - HEB"
Write-Host "============================================================"
Write-Host "Python     : $PythonExe"
Write-Host "Categoria  : $Category"
Write-Host "Reintentos : $RetryFailedAtEnd"
Write-Host "Chrome     : SESION EXISTENTE"
Write-Host "CDP        : $CdpUrl"
Write-Host ""

$ArgsList = @(
    ".\scripts\test_heb.py",
    "--category", $Category,
    "--retry-failed-at-end", "$RetryFailedAtEnd",
    "--cdp-url", $CdpUrl
)

& $PythonExe @ArgsList
$code = $LASTEXITCODE

Write-Host ""
Write-Host "Salida: $ProjectDir\output\heb_phase5_test.xlsx"
Write-Host "Diagnosticos: $ProjectDir\diagnostics\heb\"

if ($code -ne 0) {
    Write-Warning "El test HEB termino con observaciones (exit_code=$code)."
}

exit $code
