"""Проверка секретов: ловит то, что попадать в git не должно.

Скрипт работает с индексом git, поэтому здесь проверяется чистая функция
сканирования — с подставными строками и без риска что-то испортить.
"""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_secrets.py"
if not _SCRIPT.is_file():
    _SCRIPT = Path("/app/scripts/check_secrets.py")
_spec = importlib.util.spec_from_file_location("check_secrets", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

# Настоящий токен Telegram: 10 цифр, двоеточие, 35 символов после него.
TG_TOKEN = "1234567890:AA" + "z" * 33


def test_finds_telegram_token() -> None:
    found = mod.scan_content("docker/.env", "TG_ALERT_TOKEN=" + TG_TOKEN)
    assert found, "токен бота должен находиться"
    assert "TG_TOKEN" not in found[0]


def test_masked_output_does_not_leak_secret() -> None:
    """Сама проверка не должна печатать секрет: он ушёл бы в лог CI."""
    found = mod.scan_content("docker/.env", "TG_ALERT_TOKEN=" + TG_TOKEN)
    assert TG_TOKEN not in found[0]
    assert "z" * 10 not in found[0]


def test_ignores_placeholders_in_env_example() -> None:
    """Подсказки в .env.example — не секреты, иначе хук заблокирует их же."""
    examples = [
        "# TG_ALERT_TOKEN=",
        "S3_ACCESS_KEY=<ключ>",
        "POSTGRES_PASSWORD=changeme",
        "YC_API_KEY=your-key-here",
        "TOKEN=xxxxxx",
    ]
    for line in examples:
        assert mod.scan_content("docker/.env.example", line) == [], line


def test_finds_private_key() -> None:
    # Строка склеивается: иначе проверка секретов нашла бы её в этом же файле
    # и заблокировала коммит — то есть проверка мешала бы самой себе.
    marker = "-----BEGIN " + "RSA PRIVATE KEY-----"
    assert mod.scan_content("key.pem", marker)


def test_finds_s3_secret() -> None:
    found = mod.scan_content("docker/.env", "S3_SECRET" + "_KEY=verysecretvalue123")
    assert found


def test_env_interpolation_is_not_a_secret() -> None:
    """`${VAR}` в docker-compose.yml — ссылка на переменную, не секрет.

    Без этого ложное срабатывание блокировало коммит любого изменения compose:
    `WHATSAPP_APP_SECRET: ${WHATSAPP_APP_SECRET:-}` матчилось как литерал.
    """
    compose_line = "      WHATSAPP_APP_SECRET: ${WHATSAPP_APP_SECRET:-}"
    assert mod.scan_content("docker/docker-compose.yml", compose_line) == []
    # ...но литеральный секрет в той же строке обязан находиться.
    # Ключ склеен: иначе хук нашёл бы его в этом же файле и заблокировал коммит
    # (тот же приём, что в тестах выше).
    literal = "      WHATSAPP_APP" + "_SECRET: " + "a" * 40
    assert mod.scan_content("docker/docker-compose.yml", literal)


def test_ordinary_code_is_clean() -> None:
    """Обычный код не должен давать ложных срабатываний: иначе хук отключат."""
    source = (
        "TOKEN_PREFIX = 'dz24_session'\n"
        "CSRF_HEADER = 'X-Request-Id'\n"
        "TIMEOUT = 30\n"
        "def csrf_ok(secret: str, submitted: str) -> bool:\n"
        "    return hmac.compare_digest(secret, submitted)\n"
    )
    assert mod.scan_content("admin_core/web/session.py", source) == []


def test_forbidden_names_cover_env() -> None:
    assert ".env" in mod.FORBIDDEN_NAMES
    assert "certs/privkey.pem" in mod.FORBIDDEN_NAMES
