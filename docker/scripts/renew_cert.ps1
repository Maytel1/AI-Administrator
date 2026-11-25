<#
  Продление TLS-сертификата dejurnii24.duckdns.org (Let's Encrypt, DNS-01 DuckDNS).

  Запускается задачей Task Scheduler «Dezhurny24-CertRenew» раз в неделю.
  acme.sh сам решает, подошёл ли срок (продлевает за ~30 дней до конца),
  и пропускает запуск, если до срока далеко, — частые прогоны безвредны.
  После фактического продления сертификат кладётся в docker/certs и nginx
  перезапускается; если срок не подошёл, nginx не трогается.

  Ручной запуск (из любого каталога):
      powershell -NoProfile -ExecutionPolicy Bypass -File scripts\renew_cert.ps1
      powershell -NoProfile -ExecutionPolicy Bypass -File scripts\renew_cert.ps1 -Force

  Коды возврата: 0 — продлено либо срок не подошёл; 1 — ошибка.
  Токен DuckDNS читается из docker/.env и в лог не попадает.
#>
param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$docker = (Resolve-Path "$PSScriptRoot\..").Path
$acmeDir = Join-Path $docker 'certs\acme'
$certsDir = Join-Path $docker 'certs'
$domain = 'dejurnii24.duckdns.org'
$domainDir = Join-Path $acmeDir "$($domain)_ecc"

# Токен DuckDNS из docker/.env (как это делает ddns_duckdns.ps1).
$token = $null
Get-Content (Join-Path $docker '.env') | ForEach-Object {
    if ($_ -match '^DUCKDNS_TOKEN=(.*)$') { $token = $Matches[1].Trim() }
}
if (-not $token) { Write-Host 'DUCKDNS_TOKEN не найден в docker/.env' -ForegroundColor Red; exit 1 }

$fullchain = Join-Path $domainDir 'fullchain.cer'
if (-not (Test-Path $fullchain)) {
    Write-Host "нет $fullchain — сначала выпустите сертификат (см. PILOT_ONBOARDING_CHECKLIST §A2)" -ForegroundColor Red
    exit 1
}

# acme.sh сам пропустит, если до конца срока больше 30 дней. Важно: в этом
# случае он завершается с НЕнулевым кодом, поэтому пропуск определяем по
# выводу, а не по коду выхода. Маркер именно «Skipping. Next renewal» —
# -cmatch регистрозависим: в выводе успешного продления есть «skipping
# dns-01» («already verified»), и без cmatch скрипт ложно пропускал продление.
$acmeArgs = @('--renew', '-d', $domain, '--ecc', '--server', 'letsencrypt')
if ($Force) { $acmeArgs += '--force' }
$acmeOut = docker run --rm -e DuckDNS_Token="$token" -v "${acmeDir}:/acme.sh" neilpang/acme.sh @acmeArgs 2>&1
$acmeOut | ForEach-Object { Write-Host $_ }
$acmeCode = $LASTEXITCODE

if (($acmeOut -join "`n") -cmatch 'Skipping\. Next renewal') {
    Write-Host 'Срок не подошёл — acme.sh пропустил продление, nginx не тронут.'
    exit 0
}
if ($acmeCode -ne 0) { Write-Host 'acme.sh завершился с ошибкой' -ForegroundColor Red; exit 1 }

# Новый сертификат — в nginx и перезапуск (контейнер по имени: скрипт не
# зависит от рабочего каталога, задача его не гарантирует).
Copy-Item (Join-Path $domainDir 'fullchain.cer') (Join-Path $certsDir 'fullchain.pem') -Force
Copy-Item (Join-Path $domainDir "$domain.key") (Join-Path $certsDir 'privkey.pem') -Force
docker restart ai-admin-nginx | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'nginx не перезапустился — сертификат лежит в docker/certs, перезапустите вручную' -ForegroundColor Red
    exit 1
}

# Дата окончания: PS 5.1 не читает PEM конструктором X509Certificate2 —
# берём первый блок сертификата, декодируем base64 → DER.
$raw = Get-Content (Join-Path $certsDir 'fullchain.pem') -Raw
$firstBlock = ($raw -split '-+END CERTIFICATE-+')[0]
$b64 = ($firstBlock -replace '-+BEGIN CERTIFICATE-+', '' -replace '\s+', '')
$cert = [Security.Cryptography.X509Certificates.X509Certificate2]::new([Convert]::FromBase64String($b64))
Write-Host ("OK: сертификат продлён, nginx перезапущен, действителен до {0:dd.MM.yyyy}" -f $cert.NotAfter) -ForegroundColor Green
exit 0
