param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [string]$CdpUrl = "",
    [ValidateRange(1, 200)]
    [int]$MaxPages = 100
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
    throw "No se encontró Python del entorno scraper."
}
if (-not (Get-Process chrome -ErrorAction SilentlyContinue)) {
    throw "Abre Google Chrome con una pestaña de Soriana y prepara la sesión."
}
if (-not $CdpUrl) {
    $data = Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data"
    $active = @(Get-ChildItem -LiteralPath $data -Filter "DevToolsActivePort" -File -Recurse -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending)
    if (-not $active) {
        Write-Host "En Chrome: chrome://inspect/#remote-debugging"
        Write-Host "Activa Allow remote debugging for this browser instance."
        exit 11
    }
    $lines = @(Get-Content -LiteralPath $active[0].FullName)
    if ($lines.Count -lt 2) { throw "DevToolsActivePort inválido." }
    $CdpUrl = "ws://127.0.0.1:$($lines[0].Trim())$($lines[1].Trim())"
}

Write-Host "SORIANA - PILOTO GASTROINTESTINALES + DISPONIBILIDAD ONLINE"
Write-Host "Python: $PythonExe"
Write-Host "No modifica el master, ni la Fase 2 productiva."
& $PythonExe ".\scripts\test_soriana_gastro.py" --cdp-url $CdpUrl --max-pages $MaxPages
exit $LASTEXITCODE
