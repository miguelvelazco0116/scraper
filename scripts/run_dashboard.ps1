param([string]$ProjectDir = "", [string]$PythonExe = "")
$ErrorActionPreference = "Stop"
# Resolve the script directory only AFTER PowerShell has bound parameters.
# $PSScriptRoot can be empty inside default parameter expressions.
if ([string]::IsNullOrWhiteSpace($ProjectDir)) {
    $ScriptFile = $MyInvocation.MyCommand.Path
    if ([string]::IsNullOrWhiteSpace($ScriptFile)) {
        throw "No se pudo determinar la ruta del proyecto. Especifica -ProjectDir."
    }
    $ScriptFolder = Split-Path -Parent $ScriptFile
    $ProjectDir = Split-Path -Parent $ScriptFolder
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
if ([string]::IsNullOrWhiteSpace($PythonExe) -or -not (Test-Path $PythonExe)) {
    throw "No se encontro Python valido en el entorno scraper."
}
Write-Host "SCRAPER DASHBOARD - http://127.0.0.1:8501"
Write-Host "Capturas disponibles desde Streamlit > Nueva ejecucion; Fase 2 usa una sola Soriana con rotacion CP."
& $PythonExe -m streamlit run .\streamlit_app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false
exit $LASTEXITCODE
