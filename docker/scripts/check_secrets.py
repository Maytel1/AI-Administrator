"""Токены и ключи не должны попадать в git.

Почему отдельный скрипт, а не `detect-private-key`: он ловит только
SSH-ключи, а у нас в проекте — токен Telegram-бота, ключи Yandex Cloud и
S3. Любой из них в истории означает, что его надо **ротировать**: удалить
коммит не поможет, он уже ушёл на сервер.

Проверяются только файлы, которые git собирается закоммитить, плюс
`.env`-подобные имена: закоммиченный `.env` — самая частая оплошность.

    python scripts/check_secrets.py            # staged + подозрительные имена
    python docker/scripts/check_secrets.py --staged   # только то, что в индексе

Код возврата: 0 — чисто, 1 — найдено.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

# Шаблоны «значение выглядит как секрет». Значения выводятся замаскированными:
# сам скрипт не должен печатать то, от чего защищает.
PATTERNS: list[tuple[str, str]] = [
    (r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b", "токен Telegram-бота"),
    (r"\bAIza[0-9A-Za-z_\-]{35}\b", "ключ Google API"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "приватный ключ"),
    (r"\bsk-[A-Za-z0-9]{20,}\b", "ключ OpenAI-совместимого API"),
    (r"\baws_secret_access_key\s*[=:]\s*\S+", "AWS secret"),
    (r"\bYC_(API_)?SECRET\w*\s*[=:]\s*\S+", "секрет Yandex Cloud"),
    (r"\bS3_SECRET_KEY\s*[=:]\s*\S+", "ключ S3"),
    (r"\bWHATSAPP_APP_SECRET\s*[=:]\s*\S+", "секрет WhatsApp"),
]

# Имена файлов, которые не должны попадать в репозиторий ни при каких условиях.
FORBIDDEN_NAMES = (".env", ".env.local", "certs/privkey.pem", "id_rsa", "id_ed25519")

# Строки-заглушки в .env.example — они не секреты, а подсказки.
ALLOW_MARKERS = ("changeme", "example", "<токен", "<ключ", "xxxx", "your-", "...")


def mask(value: str) -> str:
    """Показать начало и длину, но не сам секрет."""
    head = value[:6]
    return f"{head}… (длина {len(value)})"


def git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return proc.stdout


def staged_files() -> list[str]:
    names = git("diff", "--cached", "--name-only", "--diff-filter=ACM")
    return [line.strip() for line in names.splitlines() if line.strip()]


def scan_content(name: str, text: str) -> list[str]:
    """Найти в содержимом файла строки, похожие на секреты."""
    found: list[str] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        lowered = line.lower()
        if any(marker in lowered for marker in ALLOW_MARKERS):
            continue
        if any(line.lstrip().startswith("#") for _ in (0,)) and "=" not in line:
            continue
        for pattern, description in PATTERNS:
            match = re.search(pattern, line)
            if match:
                found.append(
                    f"{name}:{line_number}: похоже на {description}: {mask(match.group(0))}"
                )
                break
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка секретов перед коммитом")
    parser.add_argument("--staged", action="store_true", help="только индекс")
    parser.parse_args()  # флаги оставлены на будущее; ключ запуска не меняется

    files = staged_files()
    if not files:
        print("секреты: проверять нечего (в индексе пусто)")
        return 0

    problems: list[str] = []
    for name in files:
        if any(name.endswith(forbidden) or name == forbidden for forbidden in FORBIDDEN_NAMES):
            problems.append(f"{name}: такой файл не должен попадать в git")
            continue
        try:
            text = open(name, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        problems.extend(scan_content(name, text))

    if problems:
        print("НАЙДЕНЫ ПОХОЖИЕ НА СЕКРЕТЫ:")
        for problem in problems:
            print(f"  {problem}")
        print()
        print("Если это ложное срабатывание — добавьте пометку в ALLOW_MARKERS.")
        print("Если это настоящий секрет — **смените его**: он уже попадёт в историю.")
        return 1

    print(f"секреты: чисто (проверено файлов: {len(files)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
