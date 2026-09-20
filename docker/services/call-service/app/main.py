"""call-service — скелет (фаза 0). DEVELOPMENT_PLAN.md: «Фаза 3 — Голосовой контур».

Сейчас: проверяет конфигурацию, ждёт PostgreSQL, остаётся живым.
Дальше (фаза 3): Pipecat-цикл VAD → SpeechKit STT → LLM → SpeechKit TTS,
SIP-телефония, бардж-ин, латентность < 1.2 сек, уведомление о записи (152-ФЗ),
failover по FAILOVER_SCENARIOS.md (LLM недоступен → автоответчик).
"""

import asyncio
import os
import sys

import asyncpg

CONFIG_KEYS = {
    "YC_API_KEY": "Yandex Cloud — SpeechKit/YandexGPT",
    "YC_FOLDER_ID": "Yandex Cloud — каталог",
    "SIP_LOGIN": "SIP-аккаунт телефонии (фаза 3)",
    "SIP_PASSWORD": "SIP-аккаунт телефонии (фаза 3)",
}


def cfg_report() -> str:
    return "\n".join(
        f"  {key}: {'set' if os.getenv(key) else '-'} ({hint})"
        for key, hint in CONFIG_KEYS.items()
    )


async def main() -> None:
    name = os.getenv("SERVICE_NAME", "call-service")
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
    await asyncio.Event().wait()  # держим процесс живым до фазы 3


if __name__ == "__main__":
    asyncio.run(main())
