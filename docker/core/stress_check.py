"""Временный стресс-чек (запускается вручную, в git не попадает).

1. RACE: 5 параллельных create_booking на один слот — ровно 1 успех, 4 SlotTaken.
2. Повторная бронь занятого слота — SlotTakenError.
3. SEED-IDEMPOTENT: двойной запуск сидера не дублирует данные.
"""

import asyncio
import uuid

import asyncpg

from admin_core.config import get_settings
from admin_core.crm.base import SlotTakenError
from admin_core.crm.internal import InternalCRM


async def main() -> None:
    pool = await asyncpg.create_pool(get_settings().database_url, min_size=1, max_size=10)
    crm = InternalCRM(pool)
    async with pool.acquire() as conn:
        salon = await conn.fetchrow(
            "INSERT INTO salons (name) VALUES ($1) RETURNING *",
            f"race-{uuid.uuid4().hex[:6]}",
        )
        master = await conn.fetchrow(
            "INSERT INTO masters (salon_id, name) VALUES ($1, 'Racer') RETURNING *", salon["id"]
        )
        svc = await conn.fetchrow(
            "INSERT INTO services (salon_id, title, duration_min, price) "
            "VALUES ($1, 'Гонка', 60, 100) RETURNING *",
            salon["id"],
        )
        await conn.execute(
            "INSERT INTO master_services VALUES ($1, $2)", master["id"], svc["id"]
        )
        for wd in range(7):
            await conn.execute(
                "INSERT INTO working_hours VALUES ($1, $2, $3, $4)",
                master["id"], wd, __import__("datetime").time(9, 0),
                __import__("datetime").time(21, 0),
            )
        client = await conn.fetchrow(
            "INSERT INTO clients (salon_id, phone, name) VALUES ($1, '+79990000000', 'Гонщик') "
            "RETURNING *",
            salon["id"],
        )

    slots = await crm.get_free_slots(str(salon["id"]), "Гонка")
    assert slots, "нет слотов для гонки"
    target = slots[0]
    offered = [target]

    results = await asyncio.gather(
        *[
            crm.create_booking(
                str(salon["id"]), str(client["id"]), "Гонка",
                target["start_text"], offered=offered, created_by=f"bot:race{i}",
            )
            for i in range(5)
        ],
        return_exceptions=True,
    )
    ok = sum(1 for r in results if isinstance(r, dict) and r.get("ok"))
    taken = sum(1 for r in results if isinstance(r, SlotTakenError))
    print(f"RACE: ok={ok} slot_taken={taken} other={len(results) - ok - taken}")
    assert ok == 1, f"ожидали ровно 1 успешную бронь, получили {ok}"
    assert taken == 4, f"ожидали 4 SlotTaken, получили {taken}"

    try:
        await crm.create_booking(
            str(salon["id"]), str(client["id"]), "Гонка",
            target["start_text"], offered=offered,
        )
        raise AssertionError("повторная бронь на занятый слот прошла!")
    except SlotTakenError:
        print("IDEMPOTENT(booking): повторная бронь отклонена")

    await pool.execute("DELETE FROM salons WHERE id = $1", salon["id"])

    from seed import seed  # noqa: PLC0415

    await seed(pool)
    await seed(pool)
    n_services = await pool.fetchval(
        "SELECT count(*) FROM services WHERE salon_id = (SELECT id FROM salons WHERE name='Виктория')"
    )
    n_clients = await pool.fetchval(
        "SELECT count(*) FROM clients WHERE salon_id = (SELECT id FROM salons WHERE name='Виктория')"
    )
    print(f"SEED-IDEMPOTENT: services={n_services} (норма 4), clients={n_clients} (норма 2)")
    assert n_services == 4 and n_clients == 2, "сидер задублировал данные!"

    await pool.close()
    print("STRESS: ALL OK")


asyncio.run(main())
