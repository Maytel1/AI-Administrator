"""Проверка, что восстановленная из бэкапа БД действительно содержит данные.

Зачем отдельный шаг: «pg_restore отработал без ошибок» ещё не значит, что база
пригодна. Частый случай — дамп снят с пустой или полупустой базы, и тогда
«успешное» восстановление разворачивает пустоту. Поэтому сверяем счётчики
ключевых таблиц восстановленной БД с рабочей: расхождение = копия негодна.

Логика сверки (mismatch/report) — чистые функции, покрыты автотестами без БД и
без docker. main() только снимает счётчики через asyncpg и зовёт report.

    python scripts/restore_check.py --prod aiadmin --restored aiadmin_restore_check

Код возврата: 0 — данные на месте и совпадают, 1 — расхождение или пусто,
2 — не удалось подключиться/прочитать.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_UNREACHABLE = 2

# Таблицы, по которым сверяем: они есть в базовой схеме проекта (001–004) и
# несут основные данные панели. schema_migrations дополнительно подтверждает,
# что миграции тоже попали в копию, а не одни пустые таблицы.
TABLES = (
    "salons",
    "clients",
    "bookings",
    "services",
    "masters",
    "schema_migrations",
)

DEFAULT_DSN = "postgresql://aiadmin:aiadmin_dev@localhost:5433/{db}"


def mismatch(prod: dict[str, int], restored: dict[str, int]) -> list[str]:
    """Расхождения между рабочей и восстановленной БД по счётчикам таблиц.

    ``prod`` — счётчики рабочей базы, ``restored`` — восстановленной. Значение
    None в ``restored`` означает, что таблицы там вообще нет. Пустой список =
    данные совпали. Чистая функция: не трогает БД, поэтому её можно проверять
    в автотесте без docker.
    """
    problems: list[str] = []
    for table, expected in prod.items():
        got = restored.get(table)
        if got is None:
            problems.append(f"{table}: таблица отсутствует в восстановленной БД")
        elif got != expected:
            problems.append(
                f"{table}: восстановлено {got}, в рабочей базе {expected}"
            )
    return problems


def report(prod: dict[str, int], restored: dict[str, int]) -> int:
    """Напечатать счётчики и вернуть код: 0 — совпало и непусто, иначе 1."""
    for table in TABLES:
        if table not in prod and table not in restored:
            continue
        print(f"  {table}: рабочая={prod.get(table, '-')} восстановлена={restored.get(table, 'нет')}")

    problems = mismatch(prod, restored)
    total = sum(count for count in prod.values() if count)
    if total == 0:
        print("ПРОВАЛ: рабочая база пуста — сверять нечего, бэкап бесполезен")
        return EXIT_MISMATCH
    if problems:
        print("ПРОВАЛ: восстановленная БД расходится с рабочей:")
        for problem in problems:
            print(f"  - {problem}")
        return EXIT_MISMATCH
    print("OK: восстановленная БД совпадает с рабочей по всем ключевым таблицам")
    return EXIT_OK


async def _counts(dsn: str) -> dict[str, int | None]:
    """Счётчики строк по TABLES; отсутствующая таблица -> None, не ошибка.

    Сначала смотрим, какие таблицы реально есть в public, и считаем только их:
    иначе запрос к несуществующей таблице ронял бы весь снимок, хотя для
    проверки бэкапа важно ровно обратное — зафиксировать, чего не хватает.
    """
    import asyncpg  # локально: тяжёлая зависимость нужна только в main()

    conn = await asyncpg.connect(dsn)
    try:
        present = {
            row["tablename"]
            for row in await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        }
        counts: dict[str, int | None] = {}
        for table in TABLES:
            if table not in present:
                counts[table] = None
                continue
            counts[table] = await conn.fetchval(f'SELECT count(*) FROM "{table}"')
        return counts
    finally:
        await conn.close()


async def _run(prod_db: str, restored_db: str, dsn: str) -> int:
    template = dsn if "{db}" in dsn else DEFAULT_DSN
    try:
        prod = await _counts(template.format(db=prod_db))
        restored = await _counts(template.format(db=restored_db))
    except Exception as exc:  # noqa: BLE001 — текст ошибки важнее её типа
        print(f"ОШИБКА: не удалось прочитать счётчики: {type(exc).__name__}: {exc}")
        return EXIT_UNREACHABLE
    print(f"Сверка '{prod_db}' (рабочая) и '{restored_db}' (восстановленная):")
    return report(prod, restored)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сверка восстановленной БД с рабочей")
    parser.add_argument("--prod", default=os.getenv("POSTGRES_DB", "aiadmin"))
    parser.add_argument("--restored", default="aiadmin_restore_check")
    parser.add_argument(
        "--dsn",
        default=os.getenv("DATABASE_URL", DEFAULT_DSN),
        help="DSN с подстановкой {db} или без неё",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.prod, args.restored, args.dsn))


if __name__ == "__main__":
    sys.exit(main())
