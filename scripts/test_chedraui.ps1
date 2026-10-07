param(
    [ValidateSet("higiene-bucal", "lavanderia", "all")]
    [string]$Category = "higiene-bucal",
    [string]$ProfileDir = ".chedraui_profile",
    [switch]$Headed,
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectDir

if ([string]::IsNullOrWhiteSpace($PythonExe) -and $env:VIRTUAL_ENV) {
    $candidate = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
    if (Test-Path $candidate) {
        $PythonExe = $candidate
    }
}

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $candidate = "C:\Proyectos\venvs\scraper\Scripts\python.exe"
    if (Test-Path $candidate) {
        $PythonExe = $candidate
    }
}

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $PythonExe = "python"
}

$ArgsList = @(
    ".\scripts\test_chedraui.py",
    "--category", $Category,
    "--profile-dir", $ProfileDir
)

if ($Headed) {
    $ArgsList += "--headed"
}

& $PythonExe @ArgsList
exit $LASTEXITCODE
