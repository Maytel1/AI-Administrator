"""Пул соединений + миграционный раннер.

Инициализация свежей БД происходит через docker-entrypoint-initdb.d
(001–004 в docker/postgres/init). Этот раннер нужен для последующих
миграций: помечает базовую линию и накатывает новые *.sql из migrations/ по
таблице schema_migrations.
"""

import os
import pathlib

import asyncpg

from .config import get_settings

BASELINE = [
    "001_init.sql",
    "002_schema_reference.sql",
    "003_schema_clients_bookings.sql",
    "004_schema_dialogs_ops.sql",
]

MIGRATIONS_DIR = pathlib.Path(os.getenv("MIGRATIONS_DIR", "/app/migrations"))

# Сериализация параллельных запусков раннера: bot-service и worker стартуют
# одновременно и оба зовут run_migrations (GAP_ANALYSIS §6.1).
MIGRATION_LOCK_ID = 833_221_101


async def get_pool(dsn: str | None = None) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn or get_settings().database_url, min_size=1, max_size=5
    )


async def run_migrations(pool: asyncpg.Pool) -> list[str]:
    """Идемпотентно накатывает миграции, возвращает список применённых сейчас.

    ``pg_advisory_lock`` сериализует параллельные вызовы (два инстанса при
    деплое): второй процесс ждёт и после захвата видит уже применённые имена
    в schema_migrations — повторно ничего не накатывает.
    """
    applied_now: list[str] = []
    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1)", MIGRATION_LOCK_ID)
        try:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations("
                "name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            has_base = await conn.fetchval(
                "SELECT to_regclass('public.salons') IS NOT NULL"
            )
            applied = {
                r["name"] for r in await conn.fetch("SELECT name FROM schema_migrations")
            }
            if has_base and not applied:
                # базовая линия уже накатана через docker-entrypoint-initdb.d
                for name in BASELINE:
                    await conn.execute(
                        "INSERT INTO schema_migrations(name) VALUES($1) "
                        "ON CONFLICT DO NOTHING",
                        name,
                    )
                applied |= set(BASELINE)
            if MIGRATIONS_DIR.exists():
                for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
                    if f.name in applied:
                        continue
                    async with conn.transaction():
                        await conn.execute(f.read_text(encoding="utf-8"))
                        await conn.execute(
                            "INSERT INTO schema_migrations(name) VALUES($1)", f.name
                        )
                    applied_now.append(f.name)
                    print(f"[migrations] applied {f.name}", flush=True)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", MIGRATION_LOCK_ID)
    return applied_now
