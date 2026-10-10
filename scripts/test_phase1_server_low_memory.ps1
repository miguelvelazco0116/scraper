param(
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

function Show-MemorySnapshot([string]$Label) {
    try {
        $os = Get-CimInstance Win32_OperatingSystem
        $totalMb = [math]::Round($os.TotalVisibleMemorySize / 1024)
        $freeMb = [math]::Round($os.FreePhysicalMemory / 1024)
        $usedMb = $totalMb - $freeMb
        $chrome = Get-Process chrome -ErrorAction SilentlyContinue
        $chromeMb = 0
        if ($chrome) {
            $chromeMb = [math]::Round(
                (($chrome | Measure-Object WorkingSet64 -Sum).Sum / 1MB)
            )
        }
        Write-Host "$Label | RAM used=${usedMb}MB free=${freeMb}MB total=${totalMb}MB | Chrome=${chromeMb}MB"
    } catch {
        Write-Host "$Label | memoria no disponible: $($_.Exception.Message)"
    }
}

Write-Host "========================================================================"
Write-Host "TEST SERVIDOR - FASE 1 LOW MEMORY"
Write-Host "========================================================================"
Write-Host "Python: $PythonExe"
Write-Host ""

Show-MemorySnapshot "ANTES"

& $PythonExe .\scripts\test_phase1_server_low_memory.py
$code = $LASTEXITCODE

Show-MemorySnapshot "DESPUES"

if ($code -eq 0) {
    Write-Host ""
    Write-Host "PASS: el modo low-memory completo el smoke test."
} else {
    Write-Warning "FAIL: revisa la salida antes de ejecutar la Fase 1 completa."
}

exit $code
