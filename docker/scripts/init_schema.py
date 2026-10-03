"""Применяет схему к DATABASE_URL — для CI и для свежой базы.

Локально её накатывает сам postgres через docker-entrypoint-initdb.d;
в GitHub Actions сервис postgres чистый, поэтому файлы применяются здесь.

Порядок важен и повторяет боевой:
1. ``postgres/init/*.sql`` — базовая схема (как в initdb);
2. ``core/migrations/*.sql`` — всё, что добавлялось потом, включая 006 с
   эксплуатационными таблицами. Без второго шага тесты падают на
   ``relation "reminders" does not exist`` — то есть CI проверял бы не тот код.
"""

import asyncio
import os
import pathlib
import sys

import asyncpg

ROOT = pathlib.Path(__file__).resolve().parents[1]
INIT_DIR = ROOT / "postgres" / "init"
MIGRATIONS_DIR = ROOT / "core" / "migrations"


async def main() -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL не задан")
    conn = await asyncpg.connect(url)
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations("
            "name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        for f in sorted(INIT_DIR.glob("*.sql")):
            await conn.execute(f.read_text(encoding="utf-8"))
            print(f"[init] {f.name} ok", flush=True)
        applied = {
            r["name"] for r in await conn.fetch("SELECT name FROM schema_migrations")
        }
        for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if f.name in applied:
                continue
            async with conn.transaction():
                await conn.execute(f.read_text(encoding="utf-8"))
                await conn.execute("INSERT INTO schema_migrations(name) VALUES($1)", f.name)
            print(f"[migrations] {f.name} ok", flush=True)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
