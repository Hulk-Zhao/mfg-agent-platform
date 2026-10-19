<#
  mfg-agent-platform - API watchdog

  Keeps the demo FastAPI (port 8004) alive.

  Task Scheduler runs this every couple of minutes. If /api/v2/health does
  not answer, it relaunches uvicorn as a DETACHED process (Win32_Process.Create,
  so the parent is WmiPrvSE.exe) - that is what makes it survive the terminal /
  agent session / scheduler instance that triggered the check.

  Without that detachment the API dies whenever its original owner goes away,
  which is exactly the "8004 dropped again" symptom.

  Manual use:
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\api_watchdog.ps1
      -> check only; start the API if it is down
    ... -RunApi
      -> run uvicorn in the foreground (used as the detached child)
#>
param([switch]$RunApi)

$Root      = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $Root '.venv\Scripts\python.exe'
$LogFile   = Join-Path $Root 'logs\api.log'
$HealthUrl = 'http://127.0.0.1:8004/api/v2/health'

if ($RunApi) {
    Set-Location $Root
    & $PythonExe -m uvicorn mfg_agents.v2.demo.api:app --host 0.0.0.0 --port 8004 *> $LogFile
    exit $LASTEXITCODE
}

# ---- guard 1: service already answering ----
try {
    $r = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 5
    if ($r.StatusCode -eq 200) { exit 0 }
} catch { }

# ---- guard 2: uvicorn already running but not ready yet ----
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*uvicorn*mfg_agents.v2.demo.api*' }
if ($running) { exit 0 }

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogFile) | Out-Null

$cmd = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -RunApi'
Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine      = $cmd
    CurrentDirectory = $Root
} | Out-Null
exit 0
