<#
  Ставит еженедельное продление TLS-сертификата через Планировщик Windows.

  Сам acme.sh продлевает за ~30 дней до конца срока, поэтому еженедельного
  прогона достаточно: в неположенное время он пропускает запуск без ошибок.
  Результат пишется в docker/logs/renew_cert.log — молча провалиться он
  не может.

      powershell -File scripts/setup_cert_renew_schedule.ps1
      powershell -File scripts/setup_cert_renew_schedule.ps1 -Remove
#>
param(
    [string]$At = "06:00",
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$docker = (Resolve-Path "$PSScriptRoot\..").Path
$script = Join-Path $PSScriptRoot 'renew_cert.ps1'
$logDir = Join-Path $docker 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
$log = Join-Path $logDir 'renew_cert.log'

if ($Remove) {
    Unregister-ScheduledTask -TaskName 'Dezhurny24-CertRenew' -Confirm:$false
    Write-Host "Задача Dezhurny24-CertRenew удалена" -ForegroundColor Yellow
    exit 0
}

if (-not (Test-Path $log)) { New-Item -ItemType File -Force -Path $log | Out-Null }
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (
    "-NoProfile -ExecutionPolicy Bypass -File `"$script`" *>> `"$log`""
)
# Еженедельно по понедельникам; StartWhenAvailable догоняет пропуск,
# если машина в 06:00 была выключена.
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask -TaskName 'Dezhurny24-CertRenew' -Action $action `
    -Trigger $trigger -Settings $settings `
    -Description "AI-Administrator: renew Let's Encrypt cert (docker/certs)" `
    -Force | Out-Null

Write-Host "Задача создана: понедельник $At, лог: $log" -ForegroundColor Green
Write-Host "Проверить запуск: Start-ScheduledTask -TaskName 'Dezhurny24-CertRenew'" -ForegroundColor DarkGray
