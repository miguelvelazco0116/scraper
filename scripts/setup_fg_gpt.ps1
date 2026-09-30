param(
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

Write-Host "============================================================"
Write-Host "Farmacias Guadalajara - setup GPT computer use"
Write-Host "============================================================"
Write-Host "Python: $PythonExe"
Write-Host ""

& $PythonExe -m pip install -r .\requirements-gpt.txt
if ($LASTEXITCODE -ne 0) {
    throw "No se pudieron instalar las dependencias GPT."
}

& $PythonExe -c "import openai, pyautogui, pyperclip, PIL; print('GPT dependencies OK')"
if ($LASTEXITCODE -ne 0) {
    throw "Fallo validando dependencias GPT."
}

Write-Host ""
Write-Host "SETUP GPT COMPLETADO"
Write-Host "Configura OPENAI_API_KEY antes de ejecutar el agente."
