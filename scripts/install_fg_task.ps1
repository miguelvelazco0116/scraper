param(
    [string]$TaskName = "Scraper-FarmaciasGuadalajara",
    [string]$RunAsUser = "$env:COMPUTERNAME\adm-ev",
    [string]$PythonExe = "C:\VM-py\venvs\miguel_velazco\Scripts\python.exe",
    [string]$ProjectDir = "C:\VM-py\notebooks\miguel_velazco\scraper"
)

$ErrorActionPreference = "Stop"

$Worker = Join-Path $ProjectDir "scripts\fg_worker.py"

if (-not (Test-Path $PythonExe)) {
    throw "No existe el Python del ambiente virtual: $PythonExe"
}

if (-not (Test-Path $Worker)) {
    throw "No existe el worker: $Worker"
}

Write-Host "TaskName   : $TaskName"
Write-Host "RunAsUser  : $RunAsUser"
Write-Host "PythonExe  : $PythonExe"
Write-Host "Worker     : $Worker"
Write-Host ""
Write-Host "La tarea se registrara con LogonType Interactive."
Write-Host "El usuario $RunAsUser debe tener una sesion iniciada (activa o desconectada)."
Write-Host ""

try {
    $account = New-Object System.Security.Principal.NTAccount($RunAsUser)
    $sid = $account.Translate([System.Security.Principal.SecurityIdentifier]).Value
    Write-Host "SID        : $sid"
}
catch {
    throw "No se pudo resolver el usuario Windows '$RunAsUser': $($_.Exception.Message)"
}

$actionParams = @{
    Execute = $PythonExe
    Argument = '"' + $Worker + '"'
    WorkingDirectory = $ProjectDir
}
$action = New-ScheduledTaskAction @actionParams

$principalParams = @{
    UserId = $RunAsUser
    LogonType = "Interactive"
    RunLevel = "Highest"
}
$principal = New-ScheduledTaskPrincipal @principalParams

$settingsParams = @{
    AllowStartIfOnBatteries = $true
    DontStopIfGoingOnBatteries = $true
    StartWhenAvailable = $true
    ExecutionTimeLimit = (New-TimeSpan -Hours 2)
}
$settings = New-ScheduledTaskSettingsSet @settingsParams

$taskParams = @{
    Action = $action
    Principal = $principal
    Settings = $settings
    Description = "Ejecuta Farmacias Guadalajara bajo el usuario interactivo para que el navegador no herede NT AUTHORITY\SYSTEM."
}
$task = New-ScheduledTask @taskParams

Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null

Write-Host ""
Write-Host "Tarea instalada correctamente."
Write-Host ""

Get-ScheduledTask -TaskName $TaskName |
    Select-Object TaskName, State,
        @{Name="UserId";Expression={$_.Principal.UserId}},
        @{Name="LogonType";Expression={$_.Principal.LogonType}},
        @{Name="RunLevel";Expression={$_.Principal.RunLevel}}
