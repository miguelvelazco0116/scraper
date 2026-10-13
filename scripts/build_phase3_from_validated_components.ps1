param(
    [string]$CaptureDate = "",
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = ""
)

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
if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd) { $PythonExe = $cmd.Source }
}
if ([string]::IsNullOrWhiteSpace($PythonExe) -or -not (Test-Path $PythonExe)) {
    throw "No se encontro Python valido."
}

$ArgsList = @(".\scripts\build_phase3_from_validated_components.py")
if (-not [string]::IsNullOrWhiteSpace($CaptureDate)) {
    $ArgsList += @("--capture-date", $CaptureDate)
}

& $PythonExe @ArgsList
$code = $LASTEXITCODE

if ($code -eq 0) {
    Write-Host ""
    Write-Host "Fase 3 valida construida desde sus componentes."
    Write-Host "Integrando automaticamente al concentrado diario..."

    & ".\scripts\integrate_validated_phase.ps1" `
        -Phase 3 `
        -ProjectDir $ProjectDir `
        -PythonExe $PythonExe

    $integrationCode = $LASTEXITCODE
    if ($integrationCode -ne 0) {
        Write-Warning "Fase 3 fue construida, pero NO pudo integrarse al concentrado diario."
        $code = $integrationCode
    } else {
        Write-Host "FASE 3 VALIDADA E INTEGRADA AL CONCENTRADO."
    }
}

exit $code
