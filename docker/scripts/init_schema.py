"""Применяет docker/postgres/init/*.sql к DATABASE_URL — для CI.

Локально схему накатывает сам postgres через docker-entrypoint-initdb.d;
в GitHub Actions сервис postgres чистый, поэтому файлы 001–004 применяем
этой командой (последовательность та же, что у entrypoint).
"""

import asyncio
import os
import pathlib
import sys

import asyncpg

INIT_DIR = pathlib.Path(__file__).resolve().parents[1] / "postgres" / "init"


async def main() -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL не задан")
    conn = await asyncpg.connect(url)
    try:
        for f in sorted(INIT_DIR.glob("*.sql")):
            await conn.execute(f.read_text(encoding="utf-8"))
            print(f"[init] {f.name} ok", flush=True)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
