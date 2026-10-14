param(
    [ValidateSet("higiene-bucal", "lavanderia", "desodorantes-y-antitranspirantes", "depilacion", "respiratorios", "candidates", "all", "full")]
    [string]$Category = "all",
    [ValidateSet("chedraui-polanco", "chedraui-tepeyac", "chedraui-cancun-kabah", "chedraui-puebla-centro-sur")]
    [string]$Store = "chedraui-polanco",
    [string]$ProfileDir = "",
    [ValidateRange(0, 600)]
    [int]$CategoryDelaySeconds = 0,
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
    "--store", $Store,
    "--category-delay-seconds", $CategoryDelaySeconds
)

if (-not [string]::IsNullOrWhiteSpace($ProfileDir)) {
    $ArgsList += @("--profile-dir", $ProfileDir)
}

if ($Headed) {
    $ArgsList += "--headed"
}

& $PythonExe @ArgsList
exit $LASTEXITCODE
