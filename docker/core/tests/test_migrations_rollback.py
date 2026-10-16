"""Откат миграций: rollback_sql и rollback_migrations.

Проверяем на настоящей БД: откат должен и удалить объекты, и убрать запись из
schema_migrations, а повторный прогон миграций после отката обязан снова
накатить всё с нуля — иначе схема и код разъезжаются навсегда.

ВАЖНО: откат (особенно steps=99) дропает таблицы с живыми данными —
panel_users, payments, client_legal, login_log, inbound_messages, плюс
колонки salons.paid_until и dialogs.chat_id. Поэтому ДЕСТРУКТИВНАЯ часть
выполняется в одной некоммиченной транзакции и в конце откатывается:
раньше каждый полный прогон pytest молча стирал все учётки и платежи
рабочей базы (обнаружено и исправлено 14.10.2026).
"""

from contextlib import asynccontextmanager

import pytest
from admin_core.db import rollback_migrations, run_migrations


class _TxPool:
    """Обёртка «пула»: отдаёт то же соединение, что и внешняя транзакция теста.

    run_migrations/rollback_migrations вызывают только ``pool.acquire()``,
    поэтому здесь duck-typing; вложенные ``conn.transaction()`` внутри них
    становятся SAVEPOINT'ами и откатываются вместе с внешней транзакцией.
    """

    def __init__(self, conn) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self._conn


async def test_rollback_migrations_reverts_and_reapplies(pool) -> None:
    """Откат убирает объекты миграции, повторный накат их возвращает."""
    await run_migrations(pool)
    assert await pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM schema_migrations "
        "WHERE name = '010_payments_acquiring.sql')"
    ) is True

    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            scoped = _TxPool(conn)
            rolled = await rollback_migrations(scoped, steps=1)
            assert rolled == ["011_counterparties.sql"]
            # откат 011 удалил таблицу counterparties, 010 не трогал.
            assert await conn.fetchval(
                "SELECT to_regclass('public.counterparties') IS NOT NULL"
            ) is False, "таблица counterparties должна быть удалена"
            assert await conn.fetchval(
                "SELECT to_regclass('public.client_legal') IS NOT NULL"
            ) is True, "остальные миграции не трогаются"

            reapplied = await run_migrations(scoped)
            assert "011_counterparties.sql" in reapplied
            assert await conn.fetchval(
                "SELECT to_regclass('public.counterparties') IS NOT NULL"
            ) is True, "таблица counterparties должна быть восстановлена"
        finally:
            # Не коммитим: схема и данные должны вернуться как до теста.
            await tx.rollback()


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
    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            scoped = _TxPool(conn)
            rolled = await rollback_migrations(scoped, steps=99)
            assert rolled == [
                "011_counterparties.sql", "010_payments_acquiring.sql", "009_legal.sql", "008_billing.sql",
                "007_inbound.sql", "006_ops.sql", "005_panel_users.sql",
            ]
            assert await conn.fetchval(
                "SELECT to_regclass('public.salons') IS NOT NULL"
            ) is True
            # возвращаем базу в рабочее состояние для следующих тестов
            await run_migrations(scoped)
            assert await conn.fetchval(
                "SELECT to_regclass('public.client_legal') IS NOT NULL"
            ) is True
        finally:
            # Ключевой момент: откатываем транзакцию — все «удалённые»
            # таблицы и их данные возвращаются нетронутыми.
            await tx.rollback()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
