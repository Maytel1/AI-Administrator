"""Проверка канала алертов: доходит ли сообщение до Telegram.

Зачем, если есть метрики `alert_relay_*`: метрики показывают состояние
внутри релея, но не доказывают, что сообщение **дошло до человека**. Между
релеем и вашим экраном — Worker, Telegram и ваш клиент; сбой в любом из
этих мест метриками не ловится.

Скрипт шлёт служебное сообщение через сам релей (тем же путём, что и
настоящий алерт) и ждёт подтверждения — ответа «доставлено» от Telegram
не существует, поэтому ориентируемся на то, что Bot API принял сообщение
и вернул `ok`.

    python scripts/check_alert_channel.py            # проверить сейчас
    python scripts/check_alert_channel.py --quiet    # без сообщения в чат

Код возврата: 0 — канал работает, 1 — не работает, 2 — не настроен.
Скрипт годится и для cron/планировщика: молчаливый успех невозможен —
ненулевой код завершает задачу с ошибкой.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

RELAY_URL = os.getenv("ALERT_RELAY_URL", "http://localhost:8087")
# Пометка в сообщении: без неё не отличить проверку от настоящего алерта.
MARKER = "проверка канала алертов"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NOT_CONFIGURED = 2


def send_alert(receiver: str = "telegram-alerts", timeout: float = 25.0) -> tuple[bool, str]:
    """Отправить тестовый алерт через релей тем же путём, что и настоящий.

    Идём в Alertmanager, а не прямо в релей: так проверяется вся цепочка
    целиком, включая маршрутизацию по severity. Иначе проверка показала бы
    «релей жив» там, где доставка в Telegram уже не работает.
    """
    am_url = os.getenv("ALERTMANAGER_URL", "http://localhost:9093")
    payload = [{
        "labels": {
            "alertname": "AlertChannelSelfTest",
            "severity": "critical",
            "service": "monitoring",
        },
        "annotations": {
            "summary": f"{MARKER}: канал работает",
            "description": "Сообщение отправлено автоматически, отвечать не нужно.",
        },
        "startsAt": "2026-01-01T00:00:00Z",
        "endsAt": "0001-01-01T00:00:00Z",
    }]
    url = f"{am_url}/api/v2/alerts?receiver={receiver}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return False, f"Alertmanager вернул HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}"
    except Exception as exc:  # URLError, таймаут, сбой сети
        return False, f"Alertmanager недоступен ({type(exc).__name__}: {exc})"

    # Пустой ответ 200 — Alertmanager принял алерт. Дальше он ждёт group_wait
    # (10 с для critical) и вызывает релей; ждём и смотрим его счётчики.
    if body.strip() not in ("", "[]"):
        return False, f"неожиданный ответ Alertmanager: {body[:200]}"
    return True, "алерт принят Alertmanager"


def relay_counters(url: str = RELAY_URL, timeout: float = 10.0) -> dict[str, Any]:
    """Прочитать счётчики релея: сколько доставлено и когда последний раз."""
    req = urllib.request.Request(f"{url}/health", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка канала алертов")
    parser.add_argument("--relay-url", default=RELAY_URL, help="адрес alert-relay")
    parser.add_argument("--manager-url", default=None, help="адрес Alertmanager")
    parser.add_argument("--quiet", action="store_true", help="не ждать доставки, только проверить доступность")
    args = parser.parse_args()
    if args.manager_url:
        os.environ["ALERTMANAGER_URL"] = args.manager_url

    try:
        health = relay_counters(args.relay_url)
    except Exception as exc:  # noqa: BLE001 — любой сбой = канал не проверен
        print(f"ОШИБКА: релей недоступен ({type(exc).__name__}: {exc})")
        return EXIT_FAILED

    if not health.get("configured"):
        print("ОШИБКА: релей не настроен — нет TG_ALERT_* в окружении")
        return EXIT_NOT_CONFIGURED

    print(f"релей: настроен, отправлено {health.get('sent')}, ошибок {health.get('failed')}")

    accepted, detail = send_alert()
    if not accepted:
        print(f"ОШИБКА: {detail}")
        return EXIT_FAILED
    print(f"Alertmanager: {detail}")

    if args.quiet:
        return EXIT_OK

    # Ждём доставки: Alertmanager ждёт group_wait, потом дергает релей.
    print("жду доставку (до 60 с)…")
    import time  # noqa: PLC0415 — нужен только в ветке ожидания

    before = int(health.get("sent") or 0)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        time.sleep(5)
        try:
            current = relay_counters(args.relay_url)
        except Exception:  # noqa: BLE001 — релей перезапускается, это не приговор
            continue
        if int(current.get("sent") or 0) > before:
            print(
                f"OK: сообщение доставлено (время доставки: "
                f"{current.get('last_success_at')}, всего {current.get('sent')})"
            )
            return EXIT_OK
        if int(current.get("failed") or 0) > int(health.get("failed") or 0):
            print(f"ОШИБКА: релей не смог доставить — {current.get('last_error')}")
            return EXIT_FAILED

    print("ОШИБКА: за минуту сообщение не доставлено")
    print("  проверить: docker compose logs alert-relay --tail 20")
    return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
