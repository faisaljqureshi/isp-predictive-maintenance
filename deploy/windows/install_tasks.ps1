<#
Registers three Windows scheduled tasks so everything starts at boot and restarts itself:
  ISP-Collector   python -m ispmon.collector
  ISP-Dashboard   streamlit on 127.0.0.1:8501
  ISP-Backup      daily database backup at 03:00

Run once from an elevated PowerShell:   powershell -ExecutionPolicy Bypass -File deploy\windows\install_tasks.ps1
Remove with:   Unregister-ScheduledTask -TaskName ISP-Collector,ISP-Dashboard,ISP-Backup -Confirm:$false
#>
param([string]$BackupDir = "")

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Virtual environment not found at $Python. Create it first: python -m venv .venv ; .venv\Scripts\python -m pip install -r requirements.txt"
}
if (-not (Test-Path (Join-Path $Root ".env"))) { throw ".env is missing in $Root. Copy .env.example to .env and fill it in." }

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

function Register-IspTask($Name, $Arguments, $Trigger) {
    $action = New-ScheduledTaskAction -Execute $Python -Argument $Arguments -WorkingDirectory $Root
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Trigger -Settings $settings -Principal $principal -Force | Out-Null
    Write-Host "Registered $Name"
}

$atStartup = New-ScheduledTaskTrigger -AtStartup
Register-IspTask "ISP-Collector" "-m ispmon.collector" $atStartup
Register-IspTask "ISP-Dashboard" "-m streamlit run dashboard/app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true" $atStartup

$backupArgs = "scripts\backup_db.py"
if ($BackupDir) { $backupArgs += " `"$BackupDir`"" }
Register-IspTask "ISP-Backup" $backupArgs (New-ScheduledTaskTrigger -Daily -At 3am)

Start-ScheduledTask -TaskName "ISP-Collector"
Start-ScheduledTask -TaskName "ISP-Dashboard"
Write-Host "`nCollector and dashboard started. Dashboard: http://localhost:8501   Logs: $Root\logs"
