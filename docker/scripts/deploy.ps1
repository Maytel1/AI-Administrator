# Выкатка с автооткатом: сохраняем текущие образы, ставим новые, проверяем
# здоровье и возвращаемся назад, если сервис не поднялся.
#
# Раньше шаблон заканчивался на `compose ps`: выкатка считалась успешной, даже
# если контейнер падал в рестарт. Теперь падение = откат, а не тихий сбой на проде.
param(
    [string]$Path = (Get-Location).Path,
    [string]$ComposeFiles = "docker-compose.yml -f docker-compose.prod.yml",
    [int]$HealthRetries = 12,
    [int]$HealthDelaySeconds = 5
)

$ErrorActionPreference = "Stop"
# Русские сообщения в логе деплоя иначе превращаются в кракозябры: Windows
# PowerShell 5.1 по умолчанию пишет в однобайтовой кодировке консоли.
try {
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    $OutputEncoding = [Text.Encoding]::UTF8
} catch {
    Write-Warning "не удалось переключить кодировку вывода"
}
Set-Location $Path
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backup = Join-Path $Path "deploy-backup\$stamp"
New-Item -ItemType Directory -Force -Path $backup | Out-Null

$compose = "docker compose -f docker-compose.yml -f docker-compose.prod.yml"
$services = @("web", "worker", "bot-service", "call-service", "whatsapp")

function Save-Images {
    Write-Host "[deploy] сохраняю текущие образы в $backup"
    foreach ($service in $services) {
        $image = & docker compose -f docker-compose.yml -f docker-compose.prod.yml images -q $service 2>$null
        if ($image) {
            & docker save -o (Join-Path $backup "$service.tar") $image | Out-Null
        }
    }
}

function Restore-Images {
    Write-Host "[deploy] ОТКАТ: возвращаю предыдущие образы"
    foreach ($tar in Get-ChildItem $backup -Filter *.tar -ErrorAction SilentlyContinue) {
        & docker load -i $tar.FullName | Out-Null
    }
    & docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate
    Write-Host "[deploy] откат выполнен, образы в $backup"
}

function Test-Health {
    param([int]$Retries, [int]$Delay)
    # Проверяем именно контейнеры, а не HTTP: панель может быть ещё за nginx,
    # а /health у веба слушает отдельный порт.
    for ($i = 0; $i -lt $Retries; $i++) {
        $broken = @()
        foreach ($service in $services) {
            $cid = & docker compose -f docker-compose.yml -f docker-compose.prod.yml ps -q $service 2>$null
            if (-not $cid) { $broken += "$service (нет контейнера)"; continue }
            $state = & docker inspect --format "{{.State.Status}}" $cid 2>$null
            if ($state -ne "running") { $broken += "$service ($state)" }
        }
        if ($broken.Count -eq 0) {
            Write-Host "[deploy] все сервисы запущены после $($i + 1) проверок"
            return $true
        }
        Start-Sleep -Seconds $Delay
    }
    foreach ($service in $services) {
        $cid = & docker compose -f docker-compose.yml -f docker-compose.prod.yml ps -q $service 2>$null
        $state = if ($cid) { & docker inspect --format "{{.State.Status}}" $cid 2>$null } else { "нет контейнера" }
        Write-Host ("[deploy] {0}: {1}" -f $service, $state)
    }
    return $false
}

Save-Images
Write-Host "[deploy] собираю и запускаю новую версию"
& docker compose -f docker-compose.yml -f docker-compose.prod.yml build
& docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

if (Test-Health -Retries $HealthRetries -Delay $HealthDelaySeconds) {
    Write-Host "[deploy] УСПЕХ: сервисы поднялись, старые образы в $backup"
    exit 0
}

Write-Host "[deploy] ПРОВАЛ: сервисы не поднялись, откатываюсь"
Restore-Images
exit 1
