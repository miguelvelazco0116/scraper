param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [ValidateSet("all","83420","44220","54030","97245")][string]$PostalCode = "all",
    [string]$Category = "cuidado-bucal",
    [string]$CdpUrl = "",
    [ValidateRange(1,200)][int]$MaxPages = 100
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
    throw "Python del entorno virtual scraper no encontrado."
}
if (-not (Get-Process chrome -ErrorAction SilentlyContinue)) {
    throw "Abre Chrome con una pestaña de Soriana antes de iniciar."
}
if (-not $CdpUrl) {
    $userData = Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data"
    $ports = @(
        Get-ChildItem -LiteralPath $userData -Filter "DevToolsActivePort" -File -Recurse -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending
    )
    if ($ports.Count -eq 0) {
        Write-Host "Abre chrome://inspect/#remote-debugging"
        Write-Host "Activa Allow remote debugging for this browser instance."
        exit 11
    }
    $parts = @(Get-Content -LiteralPath $ports[0].FullName)
    if ($parts.Count -lt 2) { throw "DevToolsActivePort inválido." }
    $CdpUrl = "ws://127.0.0.1:$($parts[0].Trim())$($parts[1].Trim())"
}
Write-Host "SORIANA - PILOTO DE ROTACION DE CODIGOS POSTALES"
Write-Host "Rotacion : 83420 => 44220 => 54030 => 97245"
Write-Host "Seleccion: $PostalCode | Categoria: $Category"
Write-Host "Salida   : output\pilots\soriana_cp_rotation\"
Write-Host "Master productivo: SIN CAMBIOS"
& $PythonExe ".\scripts\test_soriana_cp_rotation.py" --cdp-url $CdpUrl --cp $PostalCode --category $Category --max-pages $MaxPages
exit $LASTEXITCODE
