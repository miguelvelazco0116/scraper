param(
    [string]$StartUrl = "https://www.soriana.com/",
    [int]$RemoteDebuggingPort = 9222
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$pf = [Environment]::GetFolderPath("ProgramFiles")
$pfx86 = [Environment]::GetFolderPath("ProgramFilesX86")
$local = [Environment]::GetFolderPath("LocalApplicationData")
$chromeCandidates = @(
    (Join-Path $pf "Google\Chrome\Application\chrome.exe"),
    (Join-Path $pfx86 "Google\Chrome\Application\chrome.exe"),
    (Join-Path $local "Google\Chrome\Application\chrome.exe")
)
$Chrome = $chromeCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $Chrome) { throw "No se encontro chrome.exe." }

$Profile = Join-Path $Root ".soriana_manual_profile"
New-Item -ItemType Directory -Force -Path $Profile | Out-Null

Write-Host "Abriendo Chrome manual Soriana..."
Write-Host "Perfil : $Profile"
Write-Host "CDP    : http://127.0.0.1:$RemoteDebuggingPort"
Write-Host ""
Write-Host "Navega manualmente hasta la categoria y deja Chrome abierto."

Start-Process -FilePath $Chrome -ArgumentList @(
    "--remote-debugging-port=$RemoteDebuggingPort",
    "--user-data-dir=$Profile",
    $StartUrl
)
