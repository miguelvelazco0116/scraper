param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectDir

$scriptPath = Join-Path $ProjectDir "scripts\fg_manual_browser.js"
if (-not (Test-Path $scriptPath)) {
    throw "No existe: $scriptPath"
}

$code = Get-Content $scriptPath -Raw
Set-Clipboard -Value $code

Write-Host "============================================================"
Write-Host "Farmacias Guadalajara - modo manual asistido"
Write-Host "============================================================"
Write-Host ""
Write-Host "El script JavaScript ya esta en el portapapeles."
Write-Host ""
Write-Host "1. Abre Microsoft Edge manualmente."
Write-Host "2. Entra a la categoria de Farmacias Guadalajara."
Write-Host "3. Presiona F12 y abre Console."
Write-Host "4. Pega con Ctrl+V y presiona Enter."
Write-Host "5. Espera a que termine y descargue un JSON."
Write-Host ""
Write-Host "Despues importa el JSON con:"
Write-Host "  & .\scripts\import_fg_manual.ps1"
