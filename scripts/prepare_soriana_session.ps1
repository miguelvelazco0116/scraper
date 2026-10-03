param(
    [switch]$ForceCheck
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = "C:\Proyectos\venvs\scraper\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

$argsList = @(".\scripts\prepare_soriana_session.py")
if ($ForceCheck) {
    $argsList += "--force-check"
}

& $Python @argsList
exit $LASTEXITCODE
