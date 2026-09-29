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
Write-Host "La tarea se registrará con InteractiveToken."
Write-Host "El usuario $RunAsUser debe tener una sesión iniciada (activa o desconectada)."
Write-Host ""

$service = New-Object -ComObject "Schedule.Service"
$service.Connect()

$root = $service.GetFolder("\")
$task = $service.NewTask(0)

$task.RegistrationInfo.Description = (
    "Ejecuta Farmacias Guadalajara bajo el contexto interactivo del usuario " +
    "para evitar que Chrome/Edge herede NT AUTHORITY\SYSTEM."
)

$task.Settings.Enabled = $true
$task.Settings.AllowDemandStart = $true
$task.Settings.StartWhenAvailable = $true
$task.Settings.DisallowStartIfOnBatteries = $false
$task.Settings.StopIfGoingOnBatteries = $false
$task.Settings.AllowHardTerminate = $true
$task.Settings.ExecutionTimeLimit = "PT2H"
$task.Settings.MultipleInstances = 2

$task.Principal.UserId = $RunAsUser
$task.Principal.LogonType = 3
$task.Principal.RunLevel = 1

$action = $task.Actions.Create(0)
$action.Path = $PythonExe
$action.Arguments = '"' + $Worker + '"'
$action.WorkingDirectory = $ProjectDir

$TASK_CREATE_OR_UPDATE = 6
$TASK_LOGON_INTERACTIVE_TOKEN = 3

$null = $root.RegisterTaskDefinition(
    $TaskName,
    $task,
    $TASK_CREATE_OR_UPDATE,
    $null,
    $null,
    $TASK_LOGON_INTERACTIVE_TOKEN,
    $null
)

Write-Host ""
Write-Host "Tarea instalada correctamente."
Write-Host ""
Get-ScheduledTask -TaskName $TaskName |
    Select-Object TaskName, State, @{Name="UserId";Expression={$_.Principal.UserId}},
        @{Name="LogonType";Expression={$_.Principal.LogonType}},
        @{Name="RunLevel";Expression={$_.Principal.RunLevel}}
