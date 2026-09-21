"""Сидер демо-данных фазы 1: салон «Виктория» (Тюмень), мастера, услуги, клиенты.

Идемпотентно: повторный запуск ничего не дублирует.
Запуск: docker compose run --rm cli --seed
"""

from datetime import time

SALON_NAME = "Виктория"

SERVICES = [
    ("Стрижка женская", 90, 2500),
    ("Стрижка мужская", 60, 1200),
    ("Маникюр", 120, 2000),
    ("Окрашивание", 180, 4500),
]
MASTERS = {
    "Анна": ["Стрижка женская", "Стрижка мужская"],
    "Мария": ["Маникюр", "Окрашивание"],
}
# (телефон, имя, заметки, требует предоплату) — решение вопроса 1
CLIENTS = [
    ("+79001234567", "Иван", None, False),
    ("+79001112233", "Ольга", "Предпочитает вечернее время", True),
]


async def seed(pool) -> dict:
    async with pool.acquire() as conn:
        salon = await conn.fetchrow("SELECT * FROM salons WHERE name = $1", SALON_NAME)
        if not salon:
            salon = await conn.fetchrow(
                "INSERT INTO salons (name, phone, plan) VALUES ($1, '+7 (3452) 90-90-90', 'start') "
                "RETURNING *",
                SALON_NAME,
            )
        salon_id = salon["id"]
        for title, duration, price in SERVICES:
            await conn.execute(
                "INSERT INTO services (salon_id, title, duration_min, price) "
                "VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
                salon_id, title, duration, price,
            )
        for master_name, svc_titles in MASTERS.items():
            master = await conn.fetchrow(
                "SELECT * FROM masters WHERE salon_id = $1 AND name = $2", salon_id, master_name
            )
            if not master:
                master = await conn.fetchrow(
                    "INSERT INTO masters (salon_id, name) VALUES ($1, $2) RETURNING *",
                    salon_id, master_name,
                )
            for title in svc_titles:
                svc_id = await conn.fetchval(
                    "SELECT id FROM services WHERE salon_id = $1 AND title = $2", salon_id, title
                )
                await conn.execute(
                    "INSERT INTO master_services (master_id, service_id) VALUES ($1, $2) "
                    "ON CONFLICT DO NOTHING",
                    master["id"], svc_id,
                )
            for weekday in range(6):  # Пн–Сб 10:00–20:00
                await conn.execute(
                    "INSERT INTO working_hours (master_id, weekday, start_time, end_time) "
                    "VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
                    master["id"], weekday, time(10, 0), time(20, 0),
                )
        for phone, name, notes, prepay in CLIENTS:
            await conn.execute(
                "INSERT INTO clients (salon_id, phone, name, notes, requires_prepayment) "
                "VALUES ($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING",
                salon_id, phone, name, notes, prepay,
            )
    return {"salon": SALON_NAME, "services": len(SERVICES), "masters": len(MASTERS),
            "clients": len(CLIENTS)}
