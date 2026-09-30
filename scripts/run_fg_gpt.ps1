param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [ValidateSet("all", "cuidado-bucal", "lavanderia", "preservativos", "vias-respiratorias")]
    [string]$Category = "cuidado-bucal",
    [string]$Model = "gpt-6.1-sol"
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectDir

if ([string]::IsNullOrWhiteSpace($PythonExe) -and $env:VIRTUAL_ENV) {
    $candidate = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
    if (Test-Path $candidate) { $PythonExe = $candidate }
}

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $candidate = Join-Path $ProjectDir ".venv\Scripts\python.exe"
    if (Test-Path $candidate) { $PythonExe = $candidate }
}

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd) { $PythonExe = $cmd.Source }
}

if ([string]::IsNullOrWhiteSpace($PythonExe) -or -not (Test-Path $PythonExe)) {
    throw "No se encontro Python valido."
}

if ([string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY)) {
    throw "Falta OPENAI_API_KEY. Configurala en esta sesion de PowerShell antes de ejecutar."
}

$categories = if ($Category -eq "all") {
    @("cuidado-bucal", "lavanderia", "preservativos", "vias-respiratorias")
} else {
    @($Category)
}

Write-Host "============================================================"
Write-Host "Farmacias Guadalajara - GPT computer use"
Write-Host "============================================================"
Write-Host "Python : $PythonExe"
Write-Host "Model  : $Model"
Write-Host "Scope  : $($categories -join ', ')"
Write-Host ""
Write-Host "IMPORTANTE: no uses mouse/teclado mientras GPT controla la PC."
Write-Host "PyAutoGUI mantiene FAILSAFE: mueve el mouse a una esquina para abortar."
Write-Host ""

foreach ($cat in $categories) {
    Write-Host "------------------------------------------------------------"
    Write-Host "GPT scraping: $cat"
    Write-Host "------------------------------------------------------------"

    & $PythonExe .\scripts\fg_gpt_agent.py --category $cat --model $Model
    if ($LASTEXITCODE -ne 0) {
        throw "GPT scraper fallo en $cat con exit_code=$LASTEXITCODE"
    }
}

Write-Host ""
Write-Host "GPT SCRAPING COMPLETADO"
Write-Host "Concentrado: $ProjectDir\output\concentrado_scraper.xlsx"
