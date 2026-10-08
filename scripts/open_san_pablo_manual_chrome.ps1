param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [int]$Port = 9223,
    [string]$ProfileDir = ".san_pablo_manual_profile"
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectDir

$ChromeCandidates = @(
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)

$ChromeExe = $ChromeCandidates |
    Where-Object { Test-Path $_ } |
    Select-Object -First 1

if (-not $ChromeExe) {
    throw "No se encontro Google Chrome."
}

$ResolvedProfile = Join-Path $ProjectDir $ProfileDir

Write-Host "============================================================"
Write-Host "Farmacias San Pablo - Chrome manual"
Write-Host "============================================================"
Write-Host "Chrome : $ChromeExe"
Write-Host "Port   : $Port"
Write-Host "Profile: $ResolvedProfile"
Write-Host ""
Write-Host "1. Se abrira un Chrome dedicado."
Write-Host "2. Navega manualmente a Farmacias San Pablo."
Write-Host "3. Confirma que el sitio carga normalmente."
Write-Host "4. Deja esta ventana de Chrome abierta."
Write-Host "5. Ejecuta run_san_pablo_full.ps1 en otra consola."
Write-Host ""

$ArgsList = @(
    "--remote-debugging-port=$Port",
    "--user-data-dir=$ResolvedProfile",
    "--new-window",
    "about:blank"
)

Start-Process -FilePath $ChromeExe -ArgumentList $ArgsList
