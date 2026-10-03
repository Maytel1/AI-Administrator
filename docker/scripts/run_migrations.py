"""Накат и откат миграций.

    docker compose run --rm -T cli python scripts/run_migrations.py
    docker compose run --rm -T cli python scripts/run_migrations.py --status
    docker compose run --rm -T cli python scripts/run_migrations.py --rollback 1

`--status` обязателен перед откатом: он показывает, что именно будет
откачено, а не то, что уедет вниз по `--rollback N`.
"""

import argparse
import asyncio
import sys

sys.path.insert(0, "/app")

from admin_core.db import (  # noqa: E402
    MIGRATIONS_DIR,
    get_pool,
    rollback_migrations,
    run_migrations,
)


async def show_status(pool) -> None:
    """Что накатано в базе и что лежит в каталоге миграций."""
    rows = await pool.fetch("SELECT name, applied_at FROM schema_migrations ORDER BY name")
    applied = [row["name"] for row in rows]
    on_disk = sorted(f.name for f in MIGRATIONS_DIR.glob("*.sql"))
    print(f"[накатано] {len(applied)}")
    for name in applied:
        stamp = next(r["applied_at"] for r in rows if r["name"] == name)
        print(f"  {name:28s} {stamp:%Y-%m-%d %H:%M}")
    missing = [name for name in applied if name not in on_disk]
    if missing:
        print(f"[внимание] в каталоге нет уже накатанных: {missing}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Накат и откат миграций")
    parser.add_argument("--rollback", type=int, metavar="N", help="откатить последние N миграций")
    parser.add_argument("--status", action="store_true", help="показать состояние и выйти")
    parser.add_argument("--yes", action="store_true", help="откат без подтверждения")
    args = parser.parse_args()

    if args.rollback and args.rollback < 1:
        print("сколько миграций откатывать должно быть положительным числом")
        return 1
    if args.rollback and not args.yes:
        print(f"будет откачено миграций: {args.rollback}")
        answer = input("продолжить? (yes/no) ").strip().lower()
        if answer != "yes":
            print("отменено")
            return 1

    pool = await get_pool()
    try:
        if args.status:
            await show_status(pool)
            return 0
        if args.rollback:
            rolled = await rollback_migrations(pool, steps=args.rollback)
            print("""откачено: """, rolled or ["ничего — возможно, нет файлов .rollback.sql"])
            if not rolled:
                return 1
            return 0
        applied = await run_migrations(pool)
        print("""применено: """, applied or ["ничего"])
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
