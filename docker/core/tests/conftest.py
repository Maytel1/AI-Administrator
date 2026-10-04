"""Общие фикстуры pytest. Тесты требуют БД (docker compose up postgres)."""

import sys
import uuid
from datetime import time
from pathlib import Path

import pytest
from admin_core.crm.internal import InternalCRM
from admin_core.db import get_pool, run_migrations

# HTTP-слой панели лежит рядом с ядром, но в своём каталоге (services/web/app).
# В контейнере он уже в /app/app, а вне контейнера — в каталоге репозитория,
# поэтому без этой строки `import app.main` падал бы в CI, а не локально.
_WEB_ROOT = Path(__file__).resolve().parents[2] / "services" / "web"
if _WEB_ROOT.is_dir() and str(_WEB_ROOT) not in sys.path:
    sys.path.insert(0, str(_WEB_ROOT))

# Порог покрытия имеет смысл только на полном прогоне. При запуске одного
# файла (`pytest tests/test_health.py`) покрытие всего admin_core заведомо
# низкое, и падение было бы не про новый тест, а про соседние модули.
COVERAGE_MIN = 60


def pytest_configure(config: pytest.Config) -> None:
    """Порог покрытия — 60%, и только на полном прогоне.

    `pytest tests/test_health.py --no-cov` отключает его штатным флагом
    pytest-cov: при запуске одного файла покрытие всего admin_core заведомо
    низкое, и падать тогда было бы не на новом тесте, а на соседних модулях.
    """
    explicit = [
        arg for arg in (config.invocation_params.args or [])
        if arg.endswith(".py") or "/" in arg or "\\" in arg
    ]
    config.option.cov_fail_under = 0 if explicit else COVERAGE_MIN


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
async def booking(demo):
    """Ближайшая запись в демо-салоне: половина тестов панели работает с ней."""
    slots = await demo["crm"].get_free_slots(demo["salon"]["id"], "Тест стрижка")
    assert slots, "демо-салон должен иметь свободные слоты"
    created = await demo["crm"].create_booking(
        demo["salon"]["id"], demo["client"]["id"], "Тест стрижка",
        slots[0]["start_text"], offered=slots, created_by="test:fixtures",
    )
    assert created["ok"], created
    return {"id": created["booking_id"], "when": created["when"]}


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
