"""Сидер демо-данных фазы 1 (обновлён 29.09.2026).

Несколько датасетов под разные ситуации и вертикали (вопрос OPEN_QUESTIONS №23):
1. «Виктория» — салон красоты (beauty, исходный кейс; есть клиент с предоплатой);
2. СТС «Правый поворот» — автосервис (корпоративный клиент и разовая запись);
3. «Семейная стоматология» — клиника (VIP-пациент с заметками, «по рекомендации»);
4. Барбершоп «Опасная бритва» — мужской beauty (постоянный, абонемент).

Идемпотентно: повторный запуск ничего не дублирует.
Запуск: docker compose run --rm cli --seed
Выбор салона для CLI-диалога: DEMO_SALON='Правый' docker compose run --rm cli
"""

from datetime import time

DATASETS = [
    {
        "name": "Виктория",
        "phone": "+7 (3452) 90-90-90",
        "services": [
            ("Стрижка женская", 90, 2500),
            ("Стрижка мужская", 60, 1200),
            ("Маникюр", 120, 2000),
            ("Окрашивание", 180, 4500),
        ],
        "masters": {
            "Анна": ["Стрижка женская", "Стрижка мужская"],
            "Мария": ["Маникюр", "Окрашивание"],
        },
        "hours": (time(10, 0), time(20, 0)),  # Пн–Сб
        # (телефон, имя, заметки, требует предоплату) — решение вопроса 1
        "clients": [
            ("+79001234567", "Иван", None, False),
            ("+79001112233", "Ольга", "Предпочитает вечернее время", True),
        ],
    },
    {
        "name": "СТС «Правый поворот»",
        "phone": "+7 (3452) 45-45-45",
        "services": [
            ("Замена масла", 30, 1500),
            ("Шиномонтаж", 60, 2500),
            ("Диагностика подвески", 45, 1200),
            ("Мойка + химчистка", 90, 3000),
        ],
        "masters": {
            "Сергей": ["Замена масла", "Диагностика подвески", "Мойка + химчистка"],
            "Артём": ["Шиномонтаж", "Диагностика подвески"],
        },
        "hours": (time(9, 0), time(18, 0)),  # Пн–Сб
        "clients": [
            ("+79002345678", "Дмитрий", "Корпоративный: автопарк из 3 машин, оплата по счёту", False),
            ("+79002223344", "Павел", None, False),
        ],
    },
    {
        "name": "Семейная стоматология",
        "phone": "+7 (3452) 77-77-77",
        "services": [
            ("Осмотр", 30, 1000),
            ("Профессиональная гигиена", 60, 4000),
            ("Лечение кариеса", 90, 5500),
            ("Консультация ортодонта", 30, 1500),
        ],
        "masters": {
            "Елена": ["Осмотр", "Профессиональная гигиена", "Лечение кариеса"],
            "Игорь": ["Осмотр", "Консультация ортодонта", "Лечение кариеса"],
        },
        "hours": (time(9, 0), time(19, 0)),  # Пн–Сб
        "clients": [
            ("+79003456789", "Наталья", "VIP: аллергия на лидокаин, только Елена", False),
            ("+79003334455", "Роман", "Записался по рекомендации", False),
        ],
    },
    {
        "name": "Барбершоп «Опасная бритва»",
        "phone": "+7 (3452) 13-13-13",
        "services": [
            ("Мужская стрижка", 45, 1200),
            ("Оформление бороды", 30, 800),
            ("Классическое бритьё", 30, 900),
        ],
        "masters": {
            "Кирилл": ["Мужская стрижка", "Оформление бороды"],
            "Тимур": ["Мужская стрижка", "Классическое бритьё", "Оформление бороды"],
        },
        "hours": (time(11, 0), time(21, 0)),  # Пн–Сб, вечерний сегмент
        "clients": [
            ("+79004567890", "Алексей", "Постоянный, абонемент «8 стрижек»", False),
        ],
    },
]


async def seed(pool) -> list[dict]:
    summaries = []
    for ds in DATASETS:
        async with pool.acquire() as conn:
            salon = await conn.fetchrow("SELECT * FROM salons WHERE name = $1", ds["name"])
            if not salon:
                salon = await conn.fetchrow(
                    "INSERT INTO salons (name, phone, plan) VALUES ($1, $2, 'start') RETURNING *",
                    ds["name"], ds["phone"],
                )
            salon_id = salon["id"]
            for title, duration, price in ds["services"]:
                await conn.execute(
                    "INSERT INTO services (salon_id, title, duration_min, price) "
                    "VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
                    salon_id, title, duration, price,
                )
            start_time, end_time = ds["hours"]
            for master_name, svc_titles in ds["masters"].items():
                master = await conn.fetchrow(
                    "SELECT * FROM masters WHERE salon_id = $1 AND name = $2",
                    salon_id, master_name,
                )
                if not master:
                    master = await conn.fetchrow(
                        "INSERT INTO masters (salon_id, name) VALUES ($1, $2) RETURNING *",
                        salon_id, master_name,
                    )
                for title in svc_titles:
                    svc_id = await conn.fetchval(
                        "SELECT id FROM services WHERE salon_id = $1 AND title = $2",
                        salon_id, title,
                    )
                    await conn.execute(
                        "INSERT INTO master_services (master_id, service_id) VALUES ($1, $2) "
                        "ON CONFLICT DO NOTHING",
                        master["id"], svc_id,
                    )
                for weekday in range(6):  # Пн–Сб, часы — из датасета
                    await conn.execute(
                        "INSERT INTO working_hours (master_id, weekday, start_time, end_time) "
                        "VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
                        master["id"], weekday, start_time, end_time,
                    )
            for phone, name, notes, prepay in ds["clients"]:
                await conn.execute(
                    "INSERT INTO clients (salon_id, phone, name, notes, requires_prepayment) "
                    "VALUES ($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING",
                    salon_id, phone, name, notes, prepay,
                )
            summaries.append({
                "salon": ds["name"],
                "services": len(ds["services"]),
                "masters": len(ds["masters"]),
                "clients": len(ds["clients"]),
            })
    return summaries
