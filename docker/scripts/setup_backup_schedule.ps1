<#
  Ставит ежедневный бэкап в 4:12 утра через Планировщик Windows.

  Бэкап, который запускают руками, — это не бэкап. Расписание создаётся один
  раз и работает, пока сервер включён; результат последнего запуска пишется в
  docker/backups/backup.log, поэтому молча провалиться он не может.

      powershell -File scripts/setup_backup_schedule.ps1
      powershell -File scripts/setup_backup_schedule.ps1 -Remove
#>
param(
    [string]$At = "04:12",
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$docker = (Resolve-Path "$PSScriptRoot\..").Path
$script = Join-Path $PSScriptRoot 'backup_db.ps1'
$log = Join-Path $docker 'backups\backup.log'

if ($Remove) {
    Unregister-ScheduledTask -TaskName 'Dezhurny24-Backup' -Confirm:$false
    Write-Host "Задача Dezhurny24-Backup удалена" -ForegroundColor Yellow
    exit 0
}

if (-not (Test-Path $log)) { New-Item -ItemType File -Force -Path $log | Out-Null }
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (
    "-NoProfile -ExecutionPolicy Bypass -File `"$script`" *>> `"$log`""
)
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask -TaskName 'Dezhurny24-Backup' -Action $action `
    -Trigger $trigger -Settings $settings `
    -Description "AI-Administrator: backup PostgreSQL (docker/backups)" `
    -Force | Out-Null

Write-Host "Задача создана: ежедневно в $At, лог: $log" -ForegroundColor Green
Write-Host "Проверить запуск: Start-ScheduledTask -TaskName 'Dezhurny24-Backup'" -ForegroundColor DarkGray
