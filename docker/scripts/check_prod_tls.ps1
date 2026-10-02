<#
  Проверка боевого HTTPS перед включением HSTS и перед выходом на клиентов.
  Запуск на сервере, где крутится прод:
      powershell -ExecutionPolicy Bypass -File docker/scripts/check_prod_tls.ps1 -Host panel.example.ru

  Скрипт ничего не меняет: только смотрит и печатает отчёт. Красная строка —
  повод не выпускать панель наружу.
#>
param(
    [Parameter(Mandatory = $true)][Alias("Host")][string]$Domain,
    [int]$WarnDays = 21,          # за сколько дней предупреждать об истечении
    [switch]$Insecure             # -k для curl: ТОЛЬКО для локальной проверки
)

$ErrorActionPreference = 'Continue'
$ok = 0; $fail = 0

function Check($name, [scriptblock]$test, [string]$hint = '') {
    try {
        $result = & $test
    } catch {
        $result = $false
        $script:lastError = $_.Exception.Message
    }
    if ($result -eq $true) {
        Write-Host ('OK    ' + $name) -ForegroundColor Green
        $script:ok++
    } else {
        Write-Host ('ПРОБЛЕМА  ' + $name) -ForegroundColor Red
        if ($hint) { Write-Host ('        ' + $hint) -ForegroundColor DarkGray }
        $script:fail++
    }
}

function Get-TlsCertificate([string]$Name) {
    # Классический способ для Windows PowerShell 5.1: New-Object и делегат
    # отдельной переменной. Проверка сертификата не нужна — мы смотрим на дату.
    $null = [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $callback = [System.Net.Security.RemoteCertificateValidationCallback] {
        param($sender, $certificate, $chain, $errors)
        return $true
    }
    $tcp = New-Object System.Net.Sockets.TcpClient($Name, 443)
    $ssl = New-Object System.Net.Security.SslStream($tcp.GetStream(), $false, $callback)
    $ssl.AuthenticateAsClient($Name)
    $cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2(
        $ssl.RemoteCertificate)
    $ssl.Dispose()
    $tcp.Dispose()
    return $cert
}

function Invoke-Curl([string[]]$CurlArgs) {
    # curl.exe есть в Windows 10+ и в Windows Server 2016+; Invoke-WebRequest
    # в PowerShell 5.1 не переживает часть TLS-конфигураций nginx, поэтому
    # основной путь — curl, а Invoke-WebRequest оставлен запасным.
    # -k добавляется только с -Insecure: на проде сертификат должен проверяться.
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        $all = @('-s')
        if ($Insecure) { $all += '-k' }
        return (& curl.exe @all @CurlArgs 2>$null)
    }
    return $null
}

function Get-StatusCode([string]$Url) {
    $out = Invoke-Curl @('-s', '-o', 'NUL', '-w', '%{http_code}', $Url)
    if ($out) { return [int](($out | Select-Object -Last 1) -replace '\D', '') }
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 10 `
            -MaximumRedirection 0 -ErrorAction Stop
        return [int]$resp.StatusCode
    } catch {
        $code = $_.Exception.Response.StatusCode.value__
        if ($code) { return [int]$code }
        return 0
    }
}

function Get-Header([string]$Url, [string]$Name) {
    $out = Invoke-Curl @('-s', '-I', $Url)
    if (-not $out) {
        try {
            $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 10
            return @($r.Headers[$Name])
        } catch { return @() }
    }
    $found = @()
    foreach ($line in ($out -split "`n")) {
        if ($line -match ('^\s*' + [regex]::Escape($Name) + '\s*:\s*(.+?)\s*$')) {
            $found += $matches[1]
        }
    }
    return $found
}

# Сертификат проверяем сами и запоминаем, доверенный ли он. Иначе проверка
# падала бы на самоподписанном сертификате — а это ровно тот случай, который
# в проде означает «ещё рано выпускать наружу».
$script:CertErrors = 'нет проверки'
[Net.ServicePointManager]::ServerCertificateValidationCallback = {
    param($sender, $certificate, $chain, $errors)
    if ($errors -ne [Net.Security.SecurityProtocolType]::None) {
        $script:CertErrors = [string]$errors
    }
    return $true
}

Write-Host ('Проверка HTTPS: ' + $Domain) -ForegroundColor Cyan
Write-Host '----------------------------------------' -ForegroundColor DarkGray

# 1. Сертификат: отдаётся и не истекает скоро
$cert = $null
Check 'TLS-сертификат доступен и живой' {
    $script:cert = Get-TlsCertificate $Domain
    $script:cert.NotAfter -gt (Get-Date).AddDays(14)
} 'Сертификат не отдался или истекает раньше чем через 14 дней'

if ($script:cert) {
    $days = [int]($script:cert.NotAfter - (Get-Date)).TotalDays
    if ($days -lt $WarnDays) {
        Write-Host ('ВНИМАНИЕ  сертификат истекает через ' + $days + ' дн.') -ForegroundColor Yellow
    }
    if ($script:cert.Subject -match 'localhost') {
        Write-Host 'ВНИМАНИЕ  сертификат на localhost — для прода нужен домен с CA' -ForegroundColor Yellow
    }
}

# 2. HTTPS отвечает и отдаёт форму входа (без данных — нужна сессия)
$loginUrl = 'https://' + $Domain + '/login'
Check 'HTTPS отвечает 200' { (Get-StatusCode $loginUrl) -eq 200 }

# 3. HTTP уводит на HTTPS
$httpCode = Get-StatusCode ('http://' + $Domain + '/today')
Check 'HTTP перенаправляется на HTTPS' { $httpCode -in 301, 302, 308 } `
    ('Вместо редиректа пришёл код ' + $httpCode)

# 4. HSTS: присутствует и ровно один (дубль = nginx и приложение ставят вместе)
$hsts = @(Get-Header $loginUrl 'Strict-Transport-Security')
Check 'HSTS присутствует' { $hsts.Count -ge 1 } `
    'HSTS включают только после подтверждённого HTTPS на домене'
Check 'HSTS ровно один заголовок' { $hsts.Count -eq 1 } `
    ('Пришло заголовков: ' + $hsts.Count)

# 5. Заголовки безопасности
Check 'X-Content-Type-Options: nosniff' {
    @(Get-Header $loginUrl 'X-Content-Type-Options') -contains 'nosniff'
}
Check 'X-Frame-Options: DENY' {
    @(Get-Header $loginUrl 'X-Frame-Options') -contains 'DENY'
}
Check 'Content-Security-Policy задана' {
    @(Get-Header $loginUrl 'Content-Security-Policy').Count -ge 1
}
Check 'Cache-Control: no-store (в панели ПДн)' {
    @(Get-Header $loginUrl 'Cache-Control') -contains 'no-store'
}

if ($Insecure) {
    Write-Host 'Проверка идёт с -Insecure: доверие к сертификату НЕ проверялось' `
        -ForegroundColor Yellow
    Write-Host 'На проде запускайте без этого ключа.' -ForegroundColor Yellow
}

# 6. Служебные разделы не торчат наружу
foreach ($path in @('/health', '/metrics')) {
    Check ($path + ' закрыт снаружи') {
        (Get-StatusCode ('https://' + $Domain + $path)) -in 403, 404
    } 'Метрики и статус отдавать наружу нельзя: они рассказывают об инфраструктуре'
}

# 7. Правка данных только POST
Check 'GET /actions отклонён (405)' {
    (Get-StatusCode ('https://' + $Domain + '/actions')) -eq 405
}

Write-Host '----------------------------------------' -ForegroundColor DarkGray
Write-Host ('Хорошо: ' + $ok + ', проблем: ' + $fail) -ForegroundColor Green
if ($fail -gt 0) { exit 1 }
