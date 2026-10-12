param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [string]$WalmartProfileDir = ".walmart_profile",
    [string]$WalmartStorageState = "",
    [switch]$PrepareWalmartSession,
    [switch]$WalmartHeadless
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

$LogDir = Join-Path $ProjectDir "diagnostics\phase3_production"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogPath = Join-Path $LogDir "phase3_$Stamp.log"
Start-Transcript -Path $LogPath -Force | Out-Null

try {
    Write-Host "========================================================================"
    Write-Host "SCRAPER PRODUCTIVO - FASE 3"
    Write-Host "========================================================================"
    Write-Host "Retailers : Bodega Aurrera + Walmart"
    Write-Host "Categorias: 2 + 3 = 5"
    Write-Host "Python    : $PythonExe"
    Write-Host "Walmart   : SC Toreo (2344) / CP 11220"
    Write-Host "Perfil    : $WalmartProfileDir"
    Write-Host "Log       : $LogPath"
    Write-Host ""
    Write-Host "La fase corre secuencialmente para liberar memoria entre retailers."
    Write-Host ""

    $ProfilePath = Join-Path $ProjectDir $WalmartProfileDir
    $NeedPrepare = $PrepareWalmartSession
    if ([string]::IsNullOrWhiteSpace($WalmartStorageState) -and -not (Test-Path $ProfilePath)) {
        $NeedPrepare = $true
    }

    if ($NeedPrepare -and [string]::IsNullOrWhiteSpace($WalmartStorageState)) {
        Write-Host "Preparando sesion persistente de Walmart..."
        Write-Host "Completa manualmente cualquier verificacion y confirma SC Toreo / CP 11220."
        $PrepArgs = @(
            ".\scripts\walmart_prepare_session.py",
            "--profile-dir", $WalmartProfileDir,
            "--store-id", "2344"
        )
        & $PythonExe @PrepArgs
        if ($LASTEXITCODE -ne 0) {
            throw "No se pudo preparar la sesion de Walmart."
        }
    }

    $ArgsList = @(
        ".\scripts\run_phase3_production.py",
        "--walmart-profile-dir", $WalmartProfileDir
    )
    if (-not [string]::IsNullOrWhiteSpace($WalmartStorageState)) {
        $ArgsList += @("--walmart-storage-state", $WalmartStorageState)
    }
    if ($WalmartHeadless) {
        $ArgsList += "--walmart-headless"
    }

    & $PythonExe @ArgsList
    $code = $LASTEXITCODE

    Write-Host ""
    if ($code -eq 0) {
        Write-Host "========================================================================"
        Write-Host "FASE 3 PRODUCTIVA PUBLICADA"
        Write-Host "========================================================================"
        Write-Host "Archivo : $ProjectDir\output\concentrado_fase3.xlsx"
        Write-Host "Baseline: $ProjectDir\output\fase3_muestra_inicial_valida.xlsx"
        Write-Host "Log     : $LogPath"
    } else {
        Write-Warning "La Fase 3 NO fue publicada."
        Write-Host "Diagnostico: $ProjectDir\diagnostics\phase3_production\last_run.json"
    }

    exit $code
}
finally {
    try { Stop-Transcript | Out-Null } catch {}
}
