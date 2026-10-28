param(
    [string]$ProjectDir = "",
    [string]$PythonExe = ""
)
$ErrorActionPreference = "Stop"
# PowerShell script variables are not guaranteed during param default evaluation.
if ([string]::IsNullOrWhiteSpace($ProjectDir)) {
    $ScriptFile = $MyInvocation.MyCommand.Path
    if ([string]::IsNullOrWhiteSpace($ScriptFile)) {
        throw "No se pudo determinar la ruta del proyecto. Especifica -ProjectDir."
    }
    $ProjectDir = Split-Path -Parent (Split-Path -Parent $ScriptFile)
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
    throw "Python del entorno scraper no disponible."
}
& $PythonExe ".\scripts\test_farmacias_del_ahorro_all.py"
exit $LASTEXITCODE
