param(
  [string]$InstallDir = $PSScriptRoot,
  [string]$Config = "",
  [string]$TaskName = "ChiselWall Relay",
  [switch]$Uninstall
)
$ErrorActionPreference = "Stop"

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
  throw "请以管理员身份打开 PowerShell，再运行本脚本。"
}

if ($Uninstall) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
  Write-Host "已移除计划任务：$TaskName"
  exit 0
}

if (-not $Config) { $Config = Join-Path $InstallDir "relay.json" }
$watchdog = Join-Path $InstallDir "relay-watchdog.ps1"
if (-not (Test-Path $watchdog)) { throw "找不到 $watchdog" }
if (-not (Test-Path $Config)) { throw "找不到 relay 配置 $Config；请先运行 --init 或 admin invite" }

$taskArg = '-NoProfile -ExecutionPolicy Bypass -File "' + $watchdog + '" -InstallDir "' + $InstallDir + '" -Config "' + $Config + '"'
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $taskArg
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "已安装并启动计划任务：$TaskName"
Write-Host "日志：$InstallDir\logs\relay.log"
Write-Host "守护日志：$InstallDir\logs\relay-watchdog.log"
