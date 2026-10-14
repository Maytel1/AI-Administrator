# DuckDNS: обновление A-записи поддомена под динамический IP домашнего стенда
# (этап 2 плана развёртывания, docs/onboarding/PILOT_STAND_DEPLOY_PLAN.md).
#
# Запускается задачей Task Scheduler «Dezhurny24-DDNS» каждые 5 минут.
# Ключи читаются из docker/.env: DUCKDNS_DOMAINS, DUCKDNS_TOKEN.
#
# Коды возврата: 0 — обновлено или не настроено (пропуск), 1 — ошибка.
# Секрет (токен) в лог не попадает: пишется только результат.

$ErrorActionPreference = 'Stop'

function Write-Log([string]$message) {
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Write-Output "[$ts] $message"
}

# docker/.env рядом со скриптом (scripts/../.env): задача запускает его
# без окружения контейнера, поэтому читаем файл сами.
$envPath = Join-Path $PSScriptRoot '..\.env'
if (Test-Path $envPath) {
    foreach ($line in (Get-Content $envPath)) {
        if ($line -match '^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$') {
            Set-Item -Path "env:$($Matches[1])" -Value $Matches[2].Trim()
        }
    }
}

$token = $env:DUCKDNS_TOKEN
$domains = $env:DUCKDNS_DOMAINS
if (-not $token -or $token -eq 'changeme' -or -not $domains -or $domains -eq 'changeme.duckdns') {
    Write-Log 'ddns: DUCKDNS_TOKEN/DUCKDNS_DOMAINS не заданы — пропуск'
    exit 0
}

try {
    # ip не передаём — duckdns определяет внешний адрес сам.
    $uri = 'https://www.duckdns.org/update?domains=' + [uri]::EscapeDataString($domains) +
           '&token=' + [uri]::EscapeDataString($token)
    $raw = (Invoke-WebRequest -Uri $uri -UseBasicParsing -TimeoutSec 30).Content
    # PS 5.1 может вернуть Content как byte[] — приводим к тексту универсально.
    $resp = if ($raw -is [byte[]]) { [System.Text.Encoding]::UTF8.GetString($raw) } else { [string]$raw }
    $resp = $resp.Trim()
    if ($resp -eq 'OK') {
        Write-Log "ddns: обновлено ($domains)"
        exit 0
    }
    # Ответ KO/ошибка: токен неверен или поддомен не существует — секрет не печатаем.
    Write-Log "ddns: ОШИБКА duckdns ответил '$resp' ($domains)"
    exit 1
}
catch {
    Write-Log "ddns: СБОЙ $($_.Exception.Message)"
    exit 1
}
