param(
    [ValidateSet("Preview", "Commit")][string]$Mode = "Preview",
    [string]$CaptureDate = (Get-Date -Format "yyyy-MM-dd"),
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = ""
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
    throw "Activa el entorno Python scraper antes de ejecutar la integración."
}
Write-Host "SORIANA POSTAL / INTEGRACION PROTEGIDA"
Write-Host "Fecha        : $CaptureDate"
Write-Host "Modo         : $Mode"
Write-Host "Zona         : 83420, 44220, 54030, 97245"
Write-Host "Casos        : 24 CP/categorías"
if ($Mode -eq "Commit") {
    Write-Warning "COMMIT modifica SOLO la partición Soriana del master del día tras validar los 24 casos."
    if ($CaptureDate -ne (Get-Date -Format "yyyy-MM-dd")) {
        throw "No se permite integrar un día histórico en el master activo."
    }
    & $PythonExe ".\scripts\integrate_soriana_postal.py" --commit --day $CaptureDate
} else {
    Write-Host "PREVIEW: no modifica Excel ni puntero."
    & $PythonExe ".\scripts\integrate_soriana_postal.py" --preview --day $CaptureDate
}
exit $LASTEXITCODE
