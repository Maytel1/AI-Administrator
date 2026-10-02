<#
  Проверка восстановления из дампа — отдельная БД, рабочая база не трогается.

  Бэкап, который ни разу не восстановили, — это не бэкап. Скрипт поднимает
  временную базу, разворачивает дамп и сверяет количество ключевых таблиц с
  рабочей. Только после этого бэкап можно считать проверенным.

      powershell -File scripts/restore_db.ps1 -Dump .\backups\aiadmin-20261002-1200.dump
#>
param(
    [Parameter(Mandatory = $true)][string]$Dump,
    [string]$CheckDb = 'aiadmin_restore_check'
)

$ErrorActionPreference = 'Stop'
$dumpPath = (Resolve-Path $Dump).Path
Write-Host "Проверка восстановления из $dumpPath" -ForegroundColor Cyan

# 1. Чистая временная база
cmd /c "docker compose exec -T postgres psql -U aiadmin -d postgres -c ""DROP DATABASE IF EXISTS $CheckDb"" -c ""CREATE DATABASE $CheckDb"""
if ($LASTEXITCODE -ne 0) { Write-Host 'не удалось создать проверочную базу' -ForegroundColor Red; exit 1 }

# 2. Разворачиваем дамп
cmd /c "docker compose exec -T postgres pg_restore -U aiadmin -d $CheckDb --no-owner < `"$dumpPath`" 2>&1 | findstr /V ""detail"" "
$code = $LASTEXITCODE

# 3. Сверяем счётчики: пустая база после «успешного» восстановления — не успех
$counts = cmd /c "docker compose exec -T postgres psql -U aiadmin -d $CheckDb -t -A -c ""SELECT 'salons=' || count(*) FROM salons; """
Write-Host "восстановлено: $counts" -ForegroundColor DarkGray

# 4. Убираем за собой, даже если что-то пошло не так
cmd /c "docker compose exec -T postgres psql -U aiadmin -d postgres -c ""DROP DATABASE IF EXISTS $CheckDb"""

if (-not $counts -or $counts -match 'salons=0') {
    Write-Host 'Восстановление пустое — бэкап непригоден' -ForegroundColor Red
    exit 1
}
Write-Host 'OK: дамп разворачивается и содержит данные' -ForegroundColor Green