param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [string]$CdpUrl = "",
    [ValidateSet("all", "medicamentos-respiratorio", "higiene-bucal")]
    [string]$Category = "all",
    [ValidateRange(1, 200)]
    [int]$MaxPages = 100
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
if (-not $PythonExe -or -not (Test-Path $PythonExe)) {
    throw "Entorno virtual Python no encontrado."
}

if ([string]::IsNullOrWhiteSpace($CdpUrl)) {
    $chromeData = Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data"
    $ports = @()
    if (Test-Path $chromeData) {
        $ports = @(Get-ChildItem -Path $chromeData -Filter "DevToolsActivePort" -File -Recurse -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
    }
    if ($ports.Count -eq 0) {
        Write-Host "Abre https://www.yza.mx/ en Google Chrome."
        Write-Host "Activa chrome://inspect/#remote-debugging y prepara el CP 11590."
        exit 11
    }
    $lines = @(Get-Content -LiteralPath $ports[0].FullName)
    if ($lines.Count -lt 2) { throw "DevToolsActivePort inválido." }
    $CdpUrl = "ws://127.0.0.1:$($lines[0].Trim())$($lines[1].Trim())"
}

Write-Host "FARMACIAS YZA - PILOTO DE CAPTURA"
Write-Host "CP fijo     : 11590"
Write-Host "Categorias  : $Category"
Write-Host "Master      : NO SE MODIFICA"
Write-Host "Python      : $PythonExe"
& $PythonExe ".\scripts\test_yza.py" --category $Category --cdp-url $CdpUrl --max-pages $MaxPages
exit $LASTEXITCODE
