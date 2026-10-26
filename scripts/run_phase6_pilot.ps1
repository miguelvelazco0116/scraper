param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [ValidateSet("probe","capture")][string]$Mode = "probe",
    [ValidateSet("all","farmacias-yza","farmatodo")][string]$Retailer = "all",
    [string]$CdpUrl = "",
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
if (-not $CdpUrl) {
    $chromeData = Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data"
    $candidates = @()
    if (Test-Path $chromeData) {
        $candidates = @(Get-ChildItem -Path $chromeData -Filter "DevToolsActivePort" -File -Recurse -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending)
    }
    if ($candidates.Count -eq 0) {
        Write-Host "Abre Chrome y una pestaña de https://www.yza.mx/"
        Write-Host "Activa chrome://inspect/#remote-debugging"
        Write-Host "O pasa el endpoint local con -CdpUrl 'http://127.0.0.1:9222'"
        exit 11
    }
    $parts = @(Get-Content -LiteralPath $candidates[0].FullName)
    if ($parts.Count -lt 2) { throw "DevToolsActivePort está incompleto." }
    $CdpUrl = "ws://127.0.0.1:$($parts[0].Trim())$($parts[1].Trim())"
}
Write-Host "FASE 6 | FARMACIAS YZA (CP 11590) + FARMATODO (SIN CP)"
Write-Host "Modo: $Mode | Retailer: $Retailer | MaxPages: $MaxPages"
Write-Host "Master Fases 1-5: SIN CAMBIOS"
& $PythonExe ".\scripts\run_phase6_pilot.py" --mode $Mode --retailer $Retailer --cdp-url $CdpUrl --max-pages $MaxPages
exit $LASTEXITCODE
