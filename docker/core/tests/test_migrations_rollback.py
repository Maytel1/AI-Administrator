"""Откат миграций: rollback_sql и rollback_migrations.

Проверяем на настоящей БД: откат должен и удалить объекты, и убрать запись из
schema_migrations, а повторный прогон миграций после отката обязан снова
накатить всё с нуля — иначе схема и код разъезжаются навсегда.
"""

import pytest

from admin_core.db import rollback_migrations, run_migrations



async def test_rollback_migrations_reverts_and_reapplies(pool) -> None:
    """Откат убирает объекты миграции, повторный накат их возвращает."""
    await run_migrations(pool)
    assert await pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM schema_migrations WHERE name = '007_inbound.sql')"
    ) is True

    rolled = await rollback_migrations(pool, steps=1)
    assert rolled == ["007_inbound.sql"]
    assert await pool.fetchval(
        "SELECT to_regclass('public.inbound_messages') IS NOT NULL"
    ) is False, "реестр входящих должен исчезнуть"
    assert await pool.fetchval(
        "SELECT to_regclass('public.reminders') IS NOT NULL"
    ) is True, "остальные миграции не трогаются"

    reapplied = await run_migrations(pool)
    assert "007_inbound.sql" in reapplied
    assert await pool.fetchval(
        "SELECT to_regclass('public.inbound_messages') IS NOT NULL"
    ) is True, "повторный накат обязан вернуть таблицу"


async def test_rollback_files_are_not_migrations(pool) -> None:
    """Файлы *.rollback.sql не должны накатываться как миграции."""
    await run_migrations(pool)
    assert await pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM schema_migrations "
        "WHERE name LIKE '%.rollback.sql')"
    ) is False
    assert await pool.fetchval(
        "SELECT to_regclass('public.reminders') IS NOT NULL"
    ) is True, "накат 006 не должен тут же удалять её откатом"


async def test_rollback_keeps_baseline_intact(pool) -> None:
    """Базовые таблицы из initdb при откате остаются на месте."""
    rolled = await rollback_migrations(pool, steps=99)
    assert rolled == ["007_inbound.sql", "006_ops.sql", "005_panel_users.sql"]
    assert await pool.fetchval("SELECT to_regclass('public.salons') IS NOT NULL") is True
    # возвращаем базу в рабочее состояние для следующих тестов
    await run_migrations(pool)
    assert await pool.fetchval(
        "SELECT to_regclass('public.panel_users') IS NOT NULL"
    ) is True


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
