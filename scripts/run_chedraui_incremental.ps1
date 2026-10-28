param(
    [string]$ProjectDir = "",
    [string]$PythonExe = "",
    [switch]$Headed,
    [switch]$Preview
)
$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($ProjectDir)) {
    $ScriptFile = $MyInvocation.MyCommand.Path
    if ([string]::IsNullOrWhiteSpace($ScriptFile)) { throw "No se pudo determinar la ruta del proyecto. Especifica -ProjectDir." }
    $ProjectDir = Split-Path -Parent (Split-Path -Parent $ScriptFile)
}
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
