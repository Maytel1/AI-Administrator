"""Восстановление демо-стенда одной командой.

Зачем: прогоны тестов сносят демо-пользователя и оставляют за собой
мусорные салоны. Раньше после каждого `verify.py` приходилось вручную
создавать учётку — и она снова исчезала после следующего прогона.

    docker compose run --rm cli python -m seed_demo

Скрипт идемпотентен: запускать можно сколько угодно раз, дубликатов не
будет. Делает три вещи:

1. `seed()` — демо-салоны, услуги, мастера, клиенты (уже идемпотентно);
2. учётку владельца `DEMO_LOGIN` / `DEMO_PASSWORD`, привязанную к салону
   `DEMO_SALON`; если она есть — пароль сбрасывается на заданный;
3. чистит мусор от тестов.

Чистка мусора опаснее всего остального, поэтому условия жёсткие: салон
удаляется, только если он пустой (нет клиентов, услуг, мастеров, записей,
диалогов) И его имя совпадает с тестовым шаблоном. Салоны с данными не
трогаем никогда — даже если имя похоже на тестовое.
"""

import asyncio
import os

from admin_core.db import get_pool, run_migrations

DEMO_LOGIN = os.environ.get("DEMO_LOGIN", "demo-owner@test.ru")
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "Demo2026Pass")
DEMO_SALON = os.environ.get("DEMO_SALON", "Виктория")

# Имена, которые тесты создают сами. Пустое салон с таким именем — мусор.
TEST_NAME_PATTERNS = (
    "Chuzhoy salon",
    "Tretiy salon",
    "Другой салон",
    "audit-other-",
    "load-",
)


async def _ensure_owner(pool) -> str:
    """Создать владельца или сбросить пароль существующего."""
    from admin_core.web.auth import hash_password  # noqa: PLC0415

    row = await pool.fetchrow(
        "SELECT id FROM salons WHERE name ILIKE $1 AND is_active LIMIT 1",
        f"%{DEMO_SALON}%",
    )
    if not row:
        return (
            f"салон «{DEMO_SALON}» не найден — сначала прогоните --seed, "
            "либо задайте DEMO_SALON"
        )
    salon_id = str(row["id"])

    existing = await pool.fetchrow(
        "SELECT id FROM panel_users WHERE login = $1", DEMO_LOGIN
    )
    if existing:
        # create_user откажется «уже есть», поэтому обновляем строку напрямую:
        # create_user хеширует пароль сам, а готовой функции смены пароля нет.
        await pool.execute(
            "UPDATE panel_users SET password_hash = $2, is_active = true, "
            "role = 'owner', salon_id = $3 WHERE id = $1",
            str(existing["id"]), hash_password(DEMO_PASSWORD), salon_id,
        )
        return "пароль сброшен на заданный, учётка снова активна"

    from admin_core.web import users as web_users  # noqa: PLC0415

    result = await web_users.create_user(
        pool, login=DEMO_LOGIN, password=DEMO_PASSWORD, role="owner",
        salon_id=salon_id, name="Владелец (демо)", actor="system:seed_demo",
    )
    if not result["ok"]:
        return f"НЕ создано: {result['message']}"
    return "создана"


async def _clean_test_leftovers(pool) -> int:
    """Удалить пустые салоны с тестовыми именами. Данные не трогаем."""
    removed = 0
    rows = await pool.fetch(
        """
        SELECT s.id, s.name FROM salons s
        WHERE NOT EXISTS (SELECT 1 FROM clients c WHERE c.salon_id = s.id)
          AND NOT EXISTS (SELECT 1 FROM services sv WHERE sv.salon_id = s.id)
          AND NOT EXISTS (SELECT 1 FROM masters m WHERE m.salon_id = s.id)
          AND NOT EXISTS (SELECT 1 FROM bookings b WHERE b.salon_id = s.id)
          AND NOT EXISTS (SELECT 1 FROM dialogs d WHERE d.salon_id = s.id)
          AND NOT EXISTS (SELECT 1 FROM panel_users u WHERE u.salon_id = s.id)
        """
    )
    for row in rows:
        if not any(p.lower() in row["name"].lower() for p in TEST_NAME_PATTERNS):
            continue
        await pool.execute("DELETE FROM salons WHERE id = $1", row["id"])
        removed += 1
    return removed


async def main() -> None:
    pool = await get_pool()
    try:
        await run_migrations(pool)
        from seed import seed as seed_demo  # noqa: PLC0415

        summaries = await seed_demo(pool)
        print(f"[seed_demo] салонов с демо-данными: {len(summaries)}")

        owner = await _ensure_owner(pool)
        print(f"[seed_demo] владелец {DEMO_LOGIN}: {owner}")

        removed = await _clean_test_leftovers(pool)
        print(f"[seed_demo] удалено пустых тестовых салонов: {removed}")

        left = await pool.fetchval("SELECT count(*) FROM salons")
        print(f"[seed_demo] всего салонов в базе: {left}")
        if owner.startswith("НЕ"):
            print("[seed_demo] ВНИМАНИЕ: учётка не восстановлена, вход невозможен")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
