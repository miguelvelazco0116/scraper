param([string]$ProjectDir = (Split-Path -Parent $PSScriptRoot), [string]$PythonExe = "")
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
if ([string]::IsNullOrWhiteSpace($PythonExe) -or -not (Test-Path $PythonExe)) {
    throw "No se encontro Python valido en el entorno scraper."
}
Write-Host "SCRAPER DASHBOARD - http://127.0.0.1:8501"
Write-Host "Capturas disponibles desde Streamlit > Nueva ejecucion; Fase 2 usa una sola Soriana con rotacion CP."
& $PythonExe -m streamlit run .\streamlit_app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false
exit $LASTEXITCODE
