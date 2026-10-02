# Локальный самоподписанный сертификат: проверка HTTPS + HSTS локально.
# Запуск из корня репозитория:  powershell -File docker/scripts/make_dev_cert.ps1
# Каталог docker/certs в .gitignore: закрытый ключ наружу не попадает.
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$dir = Join-Path $root "certs"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$cert = Join-Path $dir "dev-cert.pem"
$key = Join-Path $dir "dev-key.pem"

$openssl = "C:\Program Files\Git\usr\bin\openssl.exe"
if (-not (Test-Path $openssl)) {
    $openssl = "openssl"
}
if (Test-Path $cert) {
    Write-Host "Certificate already exists: $cert"
    exit 0
}

$certArgs = @(
    "req", "-x509", "-newkey", "rsa:2048", "-nodes",
    "-keyout", $key, "-out", $cert, "-days", "825",
    "-subj", "/CN=localhost/O=Dezhurny24-dev",
    "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"
)
& $openssl @certArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host "openssl failed, code $LASTEXITCODE"
    exit 1
}
Write-Host "Done:"
Write-Host "  cert: $cert"
Write-Host "  key:  $key"
Write-Host "Add to docker/.env:"
Write-Host "  WEB_TLS_CERT=/app/certs/dev-cert.pem"
Write-Host "  WEB_TLS_KEY=/app/certs/dev-key.pem"