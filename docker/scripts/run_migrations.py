"""Накатывает миграции: docker compose run --rm -T cli python scripts/run_migrations.py"""

import asyncio
import sys

sys.path.insert(0, "/app")

from admin_core.db import get_pool, run_migrations  # noqa: E402


async def main() -> int:
    pool = await get_pool()
    try:
        applied = await run_migrations(pool)
    finally:
        await pool.close()
    print("""применено: """, applied or ["ничего"])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
