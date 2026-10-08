param(
    [ValidateSet("regression", "candidates", "all")]
    [string]$Group = "all",

    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if ([string]::IsNullOrWhiteSpace($PythonExe) -and $env:VIRTUAL_ENV) {
    $Candidate = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
    if (Test-Path $Candidate) {
        $PythonExe = $Candidate
    }
}

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $Candidate = "C:\Proyectos\venvs\scraper\Scripts\python.exe"
    if (Test-Path $Candidate) {
        $PythonExe = $Candidate
    }
}

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $PythonExe = "python"
}

& $PythonExe .\scripts\validate_scrapy_candidates.py --group $Group
exit $LASTEXITCODE
