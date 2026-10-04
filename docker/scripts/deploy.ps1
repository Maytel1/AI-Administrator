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

function Get-RunningImage {
    param([string]$Service)
    # Сохранять надо по ИМЕНИ образа, а не по ID.
    #
    # ID из `compose images -q` и из `docker inspect .Image` после
    # пересборки указывает на уже удалённый слой: `docker save` отвечает
    # «No such image», вывод уходил в $null, и скрипт рапортовал об
    # успешной выкатке, оставляя каталог отката пустым. Имя образа
    # (ai-admin-web) docker всегда разрешает, пока образ не удалён вручную.
    $cid = & docker compose -f docker-compose.yml -f docker-compose.prod.yml ps -q $Service 2>$null
    if ($cid) {
        $name = & docker inspect --format "{{.Config.Image}}" $cid 2>$null
        if ($name) { return $name.Trim() }
    }
    $image = & docker compose -f docker-compose.yml -f docker-compose.prod.yml images $Service 2>$null
    if ($image) {
        $repo = ($image | Select-Object -Skip 1 -First 1) -split '\s+' | Select-Object -First 1
        if ($repo) { return $repo.Trim() }
    }
    return ""
}

function Save-Images {
    Write-Host "[deploy] сохраняю текущие образы в $backup"
    foreach ($service in $services) {
        $image = Get-RunningImage -Service $service
        if (-not $image) {
            Write-Host "[deploy] ВНИМАНИЕ: не нашёл образ $service — откат может не сработать" -ForegroundColor Yellow
            continue
        }
        $target = Join-Path $backup "$service.tar"
        & docker save -o $target $image | Out-Null
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $target)) {
            Write-Host "[deploy] ВНИМАНИЕ: не сохранил образ $service ($image) — откат может не сработать" -ForegroundColor Yellow
        } else {
            Write-Host "[deploy]   $service ($image) -> $([math]::Round((Get-Item $target).Length / 1MB, 1)) МБ"
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
