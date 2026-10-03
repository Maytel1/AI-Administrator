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
                # *.rollback.sql — откаты к миграциям, а не сами миграции:
                # накатив их, мы бы удалили только что созданные таблицы.
                for f in [
                    f for f in sorted(MIGRATIONS_DIR.glob("*.sql"))
                    if not f.name.endswith(".rollback.sql")
                ]:
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


async def rollback_migrations(pool: asyncpg.Pool, steps: int = 1) -> list[str]:
    """Откатить последние ``steps`` миграций из папки ``migrations/``.

    Зачем: ошибочную миграцию на проде иначе чинят только восстановлением из
    бэкапа — это часы простоя. Откат выполняет парный файл ``<имя>.rollback.sql``;
    если его нет, миграция считается неоткатываемой и остаётся на месте.

    Возвращает список имён, которые действительно откатились.
    """
    files = sorted(MIGRATIONS_DIR.glob("*.sql")) if MIGRATIONS_DIR.exists() else []
    # *.rollback.sql — не миграции, а откаты к ним: иначе они накатились бы
    # как обычные миграции и удалили бы созданные таблицы.
    files = [f for f in files if not f.name.endswith(".rollback.sql")]
    if not files:
        return []
    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1)", MIGRATION_LOCK_ID)
        rolled: list[str] = []
        try:
            for f in reversed(files):
                if len(rolled) >= steps:
                    break
                if not await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM schema_migrations WHERE name = $1)",
                    f.name,
                ):
                    continue
                rollback_file = f.with_name(f.stem + ".rollback.sql")
                if not rollback_file.exists():
                    print(
                        f"[migrations] {f.name}: нет {rollback_file.name}, "
                        "откат пропущен",
                        flush=True,
                    )
                    continue
                async with conn.transaction():
                    await conn.execute(rollback_file.read_text(encoding="utf-8"))
                    await conn.execute(
                        "DELETE FROM schema_migrations WHERE name = $1", f.name
                    )
                rolled.append(f.name)
                print(f"[migrations] rolled back {f.name}", flush=True)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", MIGRATION_LOCK_ID)
    return rolled
