"""worker — скелет (фаза 0). DEVELOPMENT_PLAN.md: фазы 4–5.

Сейчас: проверяет конфигурацию, ждёт PostgreSQL, остаётся живым.
Дальше: outbox-очередь записей (фаза 4, failover из FAILOVER_SCENARIOS.md),
напоминания и подтверждения (SMS/мессенджер — фаза 2/4), рободо-звон (фаза 5),
биллинговые лимиты тарифа.
"""

import asyncio
import os
import sys

import asyncpg

CONFIG_KEYS = {
    "YC_API_KEY": "Yandex Cloud — YandexGPT/SpeechKit",
    "TELEGRAM_BOT_TOKEN": "уведомления владельцу (фаза 2)",
}


def cfg_report() -> str:
    return "\n".join(
        f"  {key}: {'set' if os.getenv(key) else '-'} ({hint})"
        for key, hint in CONFIG_KEYS.items()
    )


async def main() -> None:
    name = os.getenv("SERVICE_NAME", "worker")
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
    await asyncio.Event().wait()  # держим процесс живым


if __name__ == "__main__":
    asyncio.run(main())
