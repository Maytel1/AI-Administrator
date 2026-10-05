"""Восстановление демо-стенда: чистка мусора и идемпотентность учётки.

Главное, что тут проверяется, — что чистка НЕ сносит салоны с данными.
Ошибка в этом месте удалила бы рабочий стенд молча и «успешно».
"""

import uuid

import seed_demo


async def test_clean_removes_only_empty_test_salons(pool):
    """Пустой салон с тестовым именем удаляется, салон с клиентом — нет."""
    empty_id = await pool.fetchval(
        "INSERT INTO salons (name, phone, plan) VALUES ($1, '+70000000000', 'start') "
        "RETURNING id",
        f"Другой салон {uuid.uuid4().hex[:6]}",
    )
    with_data_id = await pool.fetchval(
        "INSERT INTO salons (name, phone, plan) VALUES ($1, '+70000000001', 'start') "
        "RETURNING id",
        f"Другой салон {uuid.uuid4().hex[:6]}",
    )
    await pool.execute(
        "INSERT INTO clients (salon_id, phone, name) VALUES ($1, '+79001110000', 'Тест')",
        with_data_id,
    )
    try:
        removed = await seed_demo._clean_test_leftovers(pool)
        assert removed >= 1
        assert not await pool.fetchval(
            "SELECT EXISTS (SELECT 1 FROM salons WHERE id = $1)", empty_id
        ), "пустой тестовый салон должен быть удалён"
        assert await pool.fetchval(
            "SELECT EXISTS (SELECT 1 FROM salons WHERE id = $1)", with_data_id
        ), "салон с клиентом трогать нельзя даже при тестовом имени"
    finally:
        await pool.execute("DELETE FROM clients WHERE salon_id = $1", with_data_id)
        await pool.execute("DELETE FROM salons WHERE id = ANY($1)", [empty_id, with_data_id])


async def test_clean_keeps_non_test_salon(pool):
    """Салон без тестового имени остаётся, даже если он пустой."""
    salon_id = await pool.fetchval(
        "INSERT INTO salons (name, phone, plan) VALUES ($1, '+70000000002', 'start') "
        "RETURNING id",
        f"Уютное место {uuid.uuid4().hex[:6]}",
    )
    try:
        await seed_demo._clean_test_leftovers(pool)
        assert await pool.fetchval(
            "SELECT EXISTS (SELECT 1 FROM salons WHERE id = $1)", salon_id
        ), "свой салон без данных чистить нельзя"
    finally:
        await pool.execute("DELETE FROM salons WHERE id = $1", salon_id)


async def test_ensure_owner_is_idempotent(pool, monkeypatch):
    """Повторный запуск не плодит учёток, а чинит пароль у существующей."""
    salon_id = await pool.fetchval(
        "INSERT INTO salons (name, phone, plan) VALUES ($1, '+70000000003', 'start') "
        "RETURNING id",
        "Салон для seed_demo",
    )
    login = f"seed-demo-{uuid.uuid4().hex[:6]}@test.ru"
    monkeypatch.setattr(seed_demo, "DEMO_LOGIN", login)
    monkeypatch.setattr(seed_demo, "DEMO_PASSWORD", "Demo2026Pass")
    monkeypatch.setattr(seed_demo, "DEMO_SALON", "Салон для seed_demo")
    try:
        first = await seed_demo._ensure_owner(pool)
        assert first == "создана", first
        second = await seed_demo._ensure_owner(pool)
        assert "пароль сброшен" in second, second
        # Одна строка на логин — дубликатов быть не должно.
        count = await pool.fetchval(
            "SELECT count(*) FROM panel_users WHERE login = $1", login
        )
        assert count == 1, f"учёток с таким логином: {count}"
        # Пароль из скрипта действительно подходит.
        from admin_core.web.users import authenticate

        assert await authenticate(pool, login, "Demo2026Pass")
    finally:
        await pool.execute("DELETE FROM panel_users WHERE login = $1", login)
        await pool.execute("DELETE FROM salons WHERE id = $1", salon_id)
