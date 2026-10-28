param(
    [string]$ProjectDir = "",
    [string]$PythonExe = "",
    [string]$ProfileDir = ".chedraui_profile",
    [switch]$Headed,
    [switch]$Headless,
    [switch]$StandardMemory,
    [ValidateRange(0, 600)]
    [int]$ChedrauiCategoryDelaySeconds = 15,
    [ValidateRange(0, 600)]
    [int]$ChedrauiStoreDelaySeconds = 60
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($ProjectDir)) {
    $ScriptFile = $MyInvocation.MyCommand.Path
    if ([string]::IsNullOrWhiteSpace($ScriptFile)) { throw "No se pudo determinar la ruta del proyecto. Especifica -ProjectDir." }
    $ProjectDir = Split-Path -Parent (Split-Path -Parent $ScriptFile)
}
Set-Location $ProjectDir

$LogDir = Join-Path $ProjectDir "diagnostics\phase1_production"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogPath = Join-Path $LogDir "phase1_$LogStamp.log"
Start-Transcript -Path $LogPath -Force | Out-Null

if ([string]::IsNullOrWhiteSpace($PythonExe) -and $env:VIRTUAL_ENV) {
    $candidate = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
    if (Test-Path $candidate) {
        $PythonExe = $candidate
    }
}

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $candidate = "C:\Proyectos\venvs\scraper\Scripts\python.exe"
    if (Test-Path $candidate) {
        $PythonExe = $candidate
    }
}

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        $PythonExe = $cmd.Source
    }
}

if ([string]::IsNullOrWhiteSpace($PythonExe) -or -not (Test-Path $PythonExe)) {
    throw "No se encontro Python valido para ejecutar la Fase 1."
}

Write-Host "========================================================================"
Write-Host "SCRAPER PRODUCTIVO - FASE 1"
Write-Host "========================================================================"
Write-Host "Retailers : Farmacias del Ahorro + Ibarra Mayoreo + Chedraui"
Write-Host "Cobertura : TODAS las categorias activas"
Write-Host "Python    : $PythonExe"
if ($Headed -and $Headless) {
    throw "Usa -Headed o -Headless, no ambos."
}

$UseHeaded = $Headed -and -not $Headless
$UseLowMemory = -not $StandardMemory

Write-Host "Profile   : $ProfileDir"
Write-Host "Ibarra    : $(if ($UseHeaded) { 'VISIBLE' } else { 'HEADLESS' })"
Write-Host "Chedraui  : VISIBLE | Directorio Estado -> Ciudad/Zona -> Tienda"
Write-Host "Memoria   : $(if ($UseLowMemory) { 'LOW-MEMORY' } else { 'STANDARD' })"
Write-Host "Chedraui  : 4 tiendas x 5 categorias"
Write-Host "Delay cat.: $ChedrauiCategoryDelaySeconds segundos"
Write-Host "Delay store: $ChedrauiStoreDelaySeconds segundos"
Write-Host "Salida    : $ProjectDir\output\concentrado_productivo.xlsx"
Write-Host "Log       : $LogPath"
Write-Host ""
Write-Host "La fase completa se publica solo con todos los casos validados."
Write-Host "El master integra cada retailer COMPLETE; los PARTIAL quedan pendientes."
Write-Host ""

$ArgsList = @(
    ".\scripts\run_phase1_production.py",
    "--profile-dir", $ProfileDir,
    "--chedraui-category-delay-seconds", $ChedrauiCategoryDelaySeconds,
    "--chedraui-store-delay-seconds", $ChedrauiStoreDelaySeconds
)

if ($UseHeaded) {
    $ArgsList += "--headed"
}

if ($UseLowMemory) {
    $ArgsList += "--low-memory"
}

& $PythonExe @ArgsList
$code = $LASTEXITCODE

Write-Host ""
if ($code -eq 0) {
    Write-Host "========================================================================"
    Write-Host "FASE 1 PRODUCTIVA PUBLICADA"
    Write-Host "========================================================================"
    Write-Host "Archivo : $ProjectDir\output\concentrado_productivo.xlsx"
    Write-Host "Baseline: $ProjectDir\output\fase1_muestra_inicial_valida.xlsx"
    Write-Host "Log     : $LogPath"
    Write-Host ""
    Write-Host "Fase 1 valida. Integrando automaticamente al concentrado diario..."

    & ".\scripts\integrate_complete_retailers.ps1" `
        -Phase 1 `
        -ProjectDir $ProjectDir `
        -PythonExe $PythonExe

    $integrationCode = $LASTEXITCODE
    if ($integrationCode -ne 0) {
        Write-Warning "Fase 1 fue publicada, pero NO pudo integrarse al concentrado diario."
        $code = $integrationCode
    } else {
        Write-Host "FASE 1 VALIDADA E INTEGRADA AL CONCENTRADO."
    }
} else {
    Write-Warning "La Fase 1 completa NO fue publicada; se evaluaran retailers completos por separado."
    Write-Host "Diagnostico: $ProjectDir\diagnostics\phase1_production\last_run.json"
    Write-Host "Log        : $LogPath"
    Write-Host "Revisando retailers completos para integrarlos sin Chedraui parcial..."
    & ".\scripts\integrate_complete_retailers.ps1" `
        -Phase 1 `
        -ProjectDir $ProjectDir `
        -PythonExe $PythonExe
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "No se pudo integrar los retailers completos de Fase 1."
    }
}

try {
    Stop-Transcript | Out-Null
} catch {
}

exit $code
