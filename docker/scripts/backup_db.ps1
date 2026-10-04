<#
  Резервная копия PostgreSQL с ротацией и проверкой целостности.

  Запуск (из каталога docker):
      powershell -File scripts/backup_db.ps1
      powershell -File scripts/backup_db.ps1 -KeepDays 30

  Что делает:
  * pg_dump -Fc в docker/backups/aiadmin-YYYYmmdd-HHMM.dump
  * проверяет, что файл не пустой и pg_restore --list проходит
  * удаляет дампы старше -KeepDays

  Молчаливый сбой бэкапа — это хуже, чем его отсутствие: через полгода вы
  обнаружите, что копий нет именно тогда, когда они понадобятся. Поэтому при
  ошибке скрипт возвращает ненулевой код и пишет причину.
#>
param(
    [int]$KeepDays = 14,
    [string]$BackupDir = "$PSScriptRoot\..\backups"
)

$ErrorActionPreference = 'Stop'
$stamp = Get-Date -Format 'yyyyMMdd-HHmm'
if (-not (Test-Path $BackupDir)) { New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null }
$target = Join-Path $BackupDir "aiadmin-$stamp.dump"

Write-Host "Бэкап БД -> $target" -ForegroundColor Cyan

# Дамп делаем через контейнер postgres: на хосте pg_dump может быть другой версии.
$dumpCmd = "docker compose exec -T postgres pg_dump -U aiadmin -d aiadmin -Fc > `"$target`""
cmd /c $dumpCmd
if ($LASTEXITCODE -ne 0) { Write-Host 'pg_dump упал' -ForegroundColor Red; exit 1 }

$size = (Get-Item $target).Length
if ($size -lt 1024) {
    Write-Host "Дамп подозрительно мал ($size байт) — возможно, база пуста или доступ закрыт" -ForegroundColor Red
    exit 1
}

# Проверка читаемости: pg_restore --list обязан разобрать заголовок дампа.
$check = cmd /c "docker compose exec -T postgres pg_restore --list < `"$target`" 2>&1"
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Дамп не читается pg_restore — копия битая' -ForegroundColor Red
    Write-Host $check
    exit 1
}

# Ротация: старые копии не нужны, но свежие должны быть на месте.
Get-ChildItem $BackupDir -Filter 'aiadmin-*.dump' |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$KeepDays) } |
    ForEach-Object { Remove-Item $_.FullName -Force; Write-Host "удалён $($_.Name)" }

$count = (Get-ChildItem $BackupDir -Filter 'aiadmin-*.dump').Count
Write-Host "OK: $size байт, копий на диске: $count (храним $KeepDays дней)" -ForegroundColor Green
Write-Host "Восстановление: powershell -File scripts/restore_db.ps1 -Dump `"$target`"" -ForegroundColor DarkGray
