<#
  Проверка восстановления из дампа — отдельная БД, рабочая база не трогается.

  Бэкап, который ни разу не восстановили, — это не бэкап. Скрипт поднимает
  временную базу, разворачивает дамп и сверяет количество ключевых таблиц с
  рабочей через scripts/restore_check.py. Только после этого бэкап можно
  считать проверенным: «pg_restore не упал» не значит, что база пригодна.

      powershell -File scripts/restore_db.ps1 -Dump .\backups\aiadmin-20261002-1200.dump
#>
param(
    [Parameter(Mandatory = $true)][string]$Dump,
    [string]$CheckDb = 'aiadmin_restore_check'
)

# Continue, а не Stop: docker/psql штатно пишут в stderr безобидные NOTICE
# (например «database ... does not exist, skipping» при DROP DATABASE IF EXISTS).
# Со Stop такой NOTICE PowerShell 5.1 считал ошибкой нативного вывода, и скрипт
# возвращал exit 1 даже при полностью успешном восстановлении — ровно в тот
# момент, когда им пользуются по-настоящему, при аварии.
$ErrorActionPreference = 'Continue'
$env:PYTHONIOENCODING = 'utf-8'
$dumpPath = (Resolve-Path $Dump).Path
Write-Host "Проверка восстановления из $dumpPath" -ForegroundColor Cyan

# 1. Чистая временная база. stderr гасим: NOTICE «does not exist» — это норма.
cmd /c "docker compose exec -T postgres psql -U aiadmin -d postgres -c ""DROP DATABASE IF EXISTS $CheckDb"" -c ""CREATE DATABASE $CheckDb"" 2>nul"
if ($LASTEXITCODE -ne 0) { Write-Host 'не удалось создать проверочную базу' -ForegroundColor Red; exit 1 }

# 2. Разворачиваем дамп. НЕ конвейерим вывод через findstr: $LASTEXITCODE после
#    конвейера в cmd — это код последней команды (findstr), а не pg_restore.
#    Успешный pg_restore с пустым выводом даёт findstr код 1, и восстановление
#    ложно объявлялось бы провалом. Поэтому снимаем код pg_restore напрямую, а
#    очистку вывода от NOTICE делаем отдельно, уже в PowerShell.
$errLog = Join-Path $env:TEMP ("restore_{0}.log" -f $PID)
cmd /c "docker compose exec -T postgres pg_restore -U aiadmin -d $CheckDb --no-owner < `"$dumpPath`" > `"$errLog`" 2>&1"
$code = $LASTEXITCODE
if (Test-Path $errLog) {
    $restoreOut = Get-Content $errLog -ErrorAction SilentlyContinue | Where-Object { $_ -notmatch 'detail' }
    if ($restoreOut) { Write-Host ($restoreOut -join "`n") -ForegroundColor DarkGray }
    Remove-Item $errLog -ErrorAction SilentlyContinue
}

# 3. Сверяем счётчики ключевых таблиц: пустая или полупустая база после
#    «успешного» восстановления — не успех. Судим по коду выхода restore_check.py
#    (0 — совпало, 1 — расхождение, 2 — нет доступа к БД): это устойчиво к
#    кодировке консоли, в отличие от сравнения текста вывода.
$verify = python "$PSScriptRoot\restore_check.py" --prod aiadmin --restored $CheckDb 2>&1
$verifyExit = $LASTEXITCODE
Write-Host $verify -ForegroundColor DarkGray

# 4. Убираем за собой, даже если что-то пошло не так
cmd /c "docker compose exec -T postgres psql -U aiadmin -d postgres -c ""DROP DATABASE IF EXISTS $CheckDb"" 2>nul" | Out-Null

if ($code -ne 0) {
    Write-Host 'pg_restore завершился с ошибкой — дамп может быть битым' -ForegroundColor Red
    exit 1
}
if ($verifyExit -ne 0) {
    Write-Host 'Восстановление непригодно — см. расхождения выше' -ForegroundColor Red
    exit 1
}
Write-Host 'OK: дамп разворачивается и содержит те же данные, что рабочая БД' -ForegroundColor Green
exit 0
