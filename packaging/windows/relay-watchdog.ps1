param(
  [string]$InstallDir = $PSScriptRoot,
  [string]$Config = ""
)
$ErrorActionPreference = "Stop"
if (-not $Config) { $Config = Join-Path $InstallDir "relay.json" }
$exe = Join-Path $InstallDir "chiselwall-relay\chiselwall-relay.exe"
$logDir = Join-Path $InstallDir "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$wrapperLog = Join-Path $logDir "relay-watchdog.log"

function Write-Wrapper([string]$Message) {
  $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message"
  Add-Content -Path $wrapperLog -Value $line -Encoding UTF8
}

Write-Wrapper "watchdog started install=$InstallDir config=$Config"
if (-not (Test-Path $exe)) {
  Write-Wrapper "ERROR executable not found: $exe"
  exit 1
}
if (-not (Test-Path $Config)) {
  Write-Wrapper "ERROR config not found: $Config"
  exit 1
}

while ($true) {
  Write-Wrapper "starting relay"
  $argLine = '--config "{0}" --host 0.0.0.0 --port 18760' -f $Config
  $proc = Start-Process -FilePath $exe -ArgumentList $argLine -WorkingDirectory $InstallDir -PassThru -WindowStyle Hidden
  Write-Wrapper "relay pid=$($proc.Id)"
  $proc.WaitForExit()
  Write-Wrapper "relay exited code=$($proc.ExitCode); restarting in 5s"
  Start-Sleep -Seconds 5
}
