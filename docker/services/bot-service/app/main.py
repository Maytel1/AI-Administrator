"""bot-service — скелет (фаза 0). DEVELOPMENT_PLAN.md: «Фаза 2 — Текстовые каналы».

Сейчас: проверяет конфигурацию, ждёт PostgreSQL, остаётся живым.
Дальше (фаза 2): aiogram (Telegram) + pywa (WhatsApp), общий роутер к «мозгу»,
кнопки «услуга → мастер → время», уведомления владельцу, эскалация в TG.
"""

import asyncio
import os
import sys

import asyncpg

CONFIG_KEYS = {
    "TELEGRAM_BOT_TOKEN": "токен @BotFather (фаза 2)",
    "YC_API_KEY": "Yandex Cloud — YandexGPT/SpeechKit",
    "YC_FOLDER_ID": "Yandex Cloud — каталог",
    "SIP_LOGIN": "телефония (фаза 3)",
}


def cfg_report() -> str:
    return "\n".join(
        f"  {key}: {'set' if os.getenv(key) else '-'} ({hint})"
        for key, hint in CONFIG_KEYS.items()
    )


async def main() -> None:
    name = os.getenv("SERVICE_NAME", "bot-service")
    print(f"[{name}] старт (фаза 0, скелет)", flush=True)
    print(f"[{name}] конфигурация:\n{cfg_report()}", flush=True)

    dsn = os.getenv("DATABASE_URL", "postgresql://aiadmin:aiadmin_dev@postgres:5432/aiadmin")
    for attempt in range(1, 31):
        try:
            conn = await asyncpg.connect(dsn, timeout=3)
            version = (await conn.fetchval("select version()")).split(",")[0]
            await conn.close()
            print(f"[{name}] PostgreSQL OK: {version}", flush=True)
            break
        except Exception as exc:  # noqa: BLE001 — скелет: просто ждём подъём БД
            print(f"[{name}] ждём PostgreSQL ({attempt}/30): {type(exc).__name__}", flush=True)
            await asyncio.sleep(2)
    else:
        print(f"[{name}] PostgreSQL так и не поднялся — выход", flush=True)
        sys.exit(1)

    print(f"[{name}] скелет готов — фаза 0 OK", flush=True)
    await asyncio.Event().wait()  # держим процесс живым до фазы 2


if __name__ == "__main__":
    asyncio.run(main())
