"""Общие фикстуры pytest. Тесты требуют БД (docker compose up postgres)."""

import uuid
from datetime import time

import pytest
from admin_core.crm.internal import InternalCRM
from admin_core.db import get_pool, run_migrations


@pytest.fixture()
async def pool():
    try:
        # Через get_pool: он ставит agreed-таймзону соединению, и тесты
        # видят ту же дату, что и приложение.
        pool = await get_pool()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"БД недоступна ({type(exc).__name__}) — запустите docker compose up")
    # Миграции накатываем здесь: тесты проверяют код, который зависит от схемы.
    # Без этого «забытая» миграция выглядит как падение в тестах, а не как
    # расхождение кода и БД.
    await run_migrations(pool)
    yield pool
    await pool.close()


@pytest.fixture()
async def demo(pool):
    """Тестовый салон + мастер + услуга + график + клиент. Каскадно чистится."""
    crm = InternalCRM(pool)
    async with pool.acquire() as conn:
        salon = await conn.fetchrow(
            "INSERT INTO salons (name, timezone) VALUES ($1, 'Asia/Yekaterinburg') RETURNING *",
            f"test-{uuid.uuid4().hex[:8]}",
        )
        master = await conn.fetchrow(
            "INSERT INTO masters (salon_id, name) VALUES ($1, 'Тест Мастер') RETURNING *",
            salon["id"],
        )
        svc = await conn.fetchrow(
            "INSERT INTO services (salon_id, title, duration_min, price) "
            "VALUES ($1, 'Тест стрижка', 60, 1000) RETURNING *",
            salon["id"],
        )
        await conn.execute(
            "INSERT INTO master_services (master_id, service_id) VALUES ($1, $2)",
            master["id"], svc["id"],
        )
        for weekday in range(7):  # все дни: чтобы «завтра» всегда был покрыт слотами
            await conn.execute(
                "INSERT INTO working_hours (master_id, weekday, start_time, end_time) "
                "VALUES ($1, $2, $3, $4)",
                master["id"], weekday, time(10, 0), time(20, 0),
            )
        client = await crm.ensure_client(
            str(salon["id"]), f"+7900{uuid.uuid4().int % 10000000:08d}", "Тест Клиент"
        )
    data = {
        "crm": crm,
        "salon": dict(salon),
        "master": dict(master),
        "service": dict(svc),
        "client": dict(client),
    }

    yield data

    await pool.execute("DELETE FROM salons WHERE id = $1", salon["id"])
