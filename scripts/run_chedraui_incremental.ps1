param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [switch]$Headed,
    [switch]$Preview
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
    throw "Python del entorno scraper no disponible."
}
$arguments = @(".\scripts\run_chedraui_incremental.py")
if ($Headed) { $arguments += "--headed" }
if ($Preview) { $arguments += "--dry-run" }
& $PythonExe @arguments
exit $LASTEXITCODE
