"""Действия панели: apply_form и его вспомогательные обработчики.

Почему отдельный файл: ``actions.py`` — единственное место, где ошибка
доходит до живого клиента (500 вместо понятного отказа). Покрытие 75%
означало, что четверть путей — создание услуги, перенос записи, лист
ожидания, ручной клиент, выдача ролей и включение 2FA — не проверена ни разу.

Здесь проверяются три вещи на каждом действии:

* отказ на плохих данных возвращает ``ok=False`` и **код** (``bad_price``,
  ``bad_phone``…), а не падает;
* успех пишет в БД то, что обещает сообщение;
* отказ не меняет данные — иначе «не сработало» осталось бы записано.
"""

import pytest
from admin_core.web import actions


async def test_service_create_rejects_price_out_of_range(demo, pool) -> None:
    """Цена вне диапазона — понятный отказ, а не запись в БД.

    Ноль в цене допустим (услуга «включено в абонемент»), поэтому в список
    мусора он не входит — иначе тест врал бы про собственные правила.
    """
    salon = str(demo["salon"]["id"])
    for price in ("99999999", "мусор", ""):
        result = await actions.apply_form(
            pool, salon,
            {"action": "service_create", "title": "X", "price": price,
             "duration_min": 60},
            actor="owner:panel",
        )
        assert result["ok"] is False, price
        assert result["error"] == "bad_price"


async def test_service_create_accepts_zero_price(demo, pool) -> None:
    """Нулевая цена — не ошибка: так оформляют включённое в абонемент."""
    salon = str(demo["salon"]["id"])
    result = await actions.apply_form(
        pool, salon,
        {"action": "service_create", "title": "В абонементе", "price": "0",
         "duration_min": 30},
        actor="owner:panel",
    )
    assert result["ok"] is True, result


async def test_service_create_rejects_bad_duration(demo, pool) -> None:
    """Длительность вне диапазона — bad_duration, а не bad_price."""
    result = await actions.apply_form(
        pool, str(demo["salon"]["id"]),
        {"action": "service_create", "title": "X", "price": "1000", "duration_min": 0},
        actor="owner:panel",
    )
    assert result["ok"] is False
    assert result["error"] == "bad_duration"


async def test_service_create_saves_and_shows_price(demo, pool) -> None:
    """Успех: услуга создана, цена читаемая, запись в БД на месте."""
    salon = str(demo["salon"]["id"])
    result = await actions.apply_form(
        pool, salon,
        {"action": "service_create", "title": "Маникюр", "price": "2 000,50",
         "duration_min": 45},
        actor="owner:boss@test.ru",
    )
    assert result["ok"] is True, result
    assert "₽" in result["message"]

    row = await pool.fetchrow(
        "SELECT price, duration_min FROM services WHERE salon_id = $1 AND title = 'Маникюр'",
        salon,
    )
    assert row is not None
    assert float(row["price"]) == pytest.approx(2000.5)
    assert row["duration_min"] == 45


async def test_booking_reschedule_rejects_bad_id_and_time(demo, booking, pool) -> None:
    """Мусорный id и мусорная дата дают разные коды отказа."""
    salon = str(demo["salon"]["id"])
    booking_id = str(booking["id"])

    bad_id = await actions.apply_form(
        pool, salon,
        {"action": "booking_reschedule", "booking_id": "не-uuid",
         "new_start": "2026-10-05 12:00"},
        actor="owner:panel",
    )
    assert bad_id["ok"] is False
    assert bad_id["error"] == "bad_id"

    bad_time = await actions.apply_form(
        pool, salon,
        {"action": "booking_reschedule", "booking_id": booking_id,
         "new_start": "позавчера вечером"},
        actor="owner:panel",
    )
    assert bad_time["ok"] is False
    assert bad_time["error"] == "bad_time"


async def test_booking_reschedule_moves_in_salon_timezone(demo, booking, pool) -> None:
    """Перенос считается в таймзоне салона, а не в UTC.

    Администратор вводит «12:00» по стенке салона. Сохрани это как UTC — и
    в отчётах запись съезжает на 5 часов: незаметно для того, кто вносил,
    и очень заметно в бухгалтерии.
    """
    salon = str(demo["salon"]["id"])
    booking_id = str(booking["id"])
    result = await actions.apply_form(
        pool, salon,
        {"action": "booking_reschedule", "booking_id": booking_id,
         "new_start": "2026-10-05 12:00"},
        actor="owner:panel",
    )
    assert result["ok"] is True, result

    stored = await pool.fetchval("SELECT starts_at FROM bookings WHERE id = $1", booking_id)
    from zoneinfo import ZoneInfo

    assert stored.astimezone(ZoneInfo("Asia/Yekaterinburg")).strftime("%H:%M") == "12:00"
async def test_waitlist_add_requires_full_phone(demo, pool) -> None:
    """Короткий телефон — отказ: иначе в лист ожидания попадёт несуществующий клиент."""
    result = await actions.apply_form(
        pool, str(demo["salon"]["id"]),
        {"action": "waitlist_add", "phone": "123", "note": "хочу на вечер"},
        actor="owner:panel",
    )
    assert result["ok"] is False
    assert result["error"] == "bad_phone"


async def test_waitlist_add_counts_queue(demo, pool) -> None:
    """Успех: клиент в очереди, её размер виден в сообщении."""
    result = await actions.apply_form(
        pool, str(demo["salon"]["id"]),
        {"action": "waitlist_add", "phone": "+79001112233", "note": "вечером"},
        actor="owner:panel",
    )
    assert result["ok"] is True, result
    assert "1" in result["message"]

    from admin_core.waitlist import list_waitlist

    queue = await list_waitlist(pool, str(demo["salon"]["id"]))
    assert len(queue) == 1


async def test_client_create_requires_phone(demo, pool) -> None:
    """Клиент без телефона бесполезен: бот узнаёт его именно по номеру."""
    result = await actions.apply_form(
        pool, str(demo["salon"]["id"]),
        {"action": "client_create", "name": "Без телефона"},
        actor="owner:panel",
    )
    assert result["ok"] is False
    assert result["error"] == "bad_phone"


async def test_client_create_saves_and_keeps_prepayment_flag(demo, pool) -> None:
    """Флаг предоплаты сохраняется — на него завязана логика напоминаний."""
    salon = str(demo["salon"]["id"])
    phone = "+79005556677"
    result = await actions.apply_form(
        pool, salon,
        {"action": "client_create", "phone": phone, "name": "Иван",
         "notes": "постоянный", "requires_prepayment": "1"},
        actor="owner:panel",
    )
    assert result["ok"] is True, result
    assert phone in result["message"]

    row = await pool.fetchrow(
        "SELECT requires_prepayment, notes FROM clients WHERE salon_id = $1 AND phone = $2",
        salon, phone,
    )
    assert row["requires_prepayment"] is True
    assert row["notes"] == "постоянный"


async def test_user_create_allows_only_self_issue_roles(demo, pool) -> None:
    """Из панели нельзя выдать роль team — это привилегия нашей команды."""
    result = await actions.apply_form(
        pool, str(demo["salon"]["id"]),
        {"action": "user_create", "login": "hacker@test.ru", "role": "team"},
        actor="owner:panel",
    )
    assert result["ok"] is False
    assert result["error"] == "bad_role"
    assert await pool.fetchval(
        "SELECT count(*) FROM panel_users WHERE login = 'hacker@test.ru'"
    ) == 0


async def test_user_create_shows_password_once(demo, pool) -> None:
    """Пароль показывается один раз, а в БД лежит только хеш."""
    login = "novichok@test.ru"
    result = await actions.apply_form(
        pool, str(demo["salon"]["id"]),
        {"action": "user_create", "login": login, "role": "staff", "name": "Новичок"},
        actor="owner:boss@test.ru",
    )
    assert result["ok"] is True, result
    assert result["once"]["login"] == login
    password = result["once"]["password"]
    assert password and password not in result["message"]

    stored = await pool.fetchval(
        "SELECT password_hash FROM panel_users WHERE login = $1", login
    )
    assert stored and password not in stored
    assert "один раз" in result["once"]["note"]


async def test_user_totp_hides_secret_from_foreign_salon(demo, pool) -> None:
    """Чужой сотрудник не должен получать секрет 2FA.

    Иначе владелец точки, запросивший чужой логин, получил бы ключ,
    открывающий чужую учётку.
    """
    result = await actions.apply_form(
        pool, str(demo["salon"]["id"]),
        {"action": "user_totp", "login": "чужой@test.ru"},
        actor="owner:panel",
    )
    assert result["ok"] is False
    assert result["error"] == "not_found"


async def test_user_totp_returns_secret_once(demo, pool) -> None:
    """Свой сотрудник получает секрет и ссылку для аутентификатора."""
    login = "svoi@test.ru"
    created = await actions.apply_form(
        pool, str(demo["salon"]["id"]),
        {"action": "user_create", "login": login, "role": "staff"},
        actor="owner:boss@test.ru",
    )
    assert created["ok"] is True

    result = await actions.apply_form(
        pool, str(demo["salon"]["id"]),
        {"action": "user_totp", "login": login},
        actor="owner:boss@test.ru",
    )
    assert result["ok"] is True, result
    secret = result["once"]["secret"]
    assert secret and result["once"]["uri"].startswith("otpauth://")
    assert "один раз" in result["once"]["note"]

    enabled = await pool.fetchval(
        "SELECT totp_enabled FROM panel_users WHERE login = $1", login
    )
    assert enabled is True


def test_helpers_return_none_instead_of_raising() -> None:
    """Разбор пользовательского ввода не должен ронять обработчик.

    Раньше мусорный UUID доходил до asyncpg и давал 500 — отказ в
    обслуживании по одному клику.
    """
    assert actions._uuid("' ; DROP TABLE services --") == ""
    assert actions._uuid(None) == ""
    assert actions._decimal("мусор") is None
    assert actions._decimal("2 000,50") == pytest.approx(2000.5)
    assert actions._int({"x": "abc"}, "x", 7) == 7
    assert actions._money(2000.4).endswith("₽")


def test_role_helpers_read_actor_string() -> None:
    """Роль и логин вытаскиваются из строки актора ``роль:логин``.

    Отдельный случай — ``admin``: это ``team`` глазами человека, и разбирать
    его как гостя значило бы потерять права в audit-записи.
    """
    assert actions._actor_role("staff:anna@test.ru") == "staff"
    assert actions._actor_login("staff:anna@test.ru") == "anna@test.ru"
    assert actions._actor_role("admin:boss@test.ru") == "team"
    # Логин с двоеточием внутри — мусор, а не роль: без прав.
    assert actions._actor_role("staff:a:b") == "guest"
    # Без двоеточия (строка из CLI) — гость и пустой логин, но без исключения.
    assert actions._actor_role("owner") == "guest"
    assert actions._actor_login("owner") == ""


async def test_booking_reschedule_keeps_record_on_failure(demo, booking, pool) -> None:
    """Неудачный перенос не должен двигать запись «на всякий случай»."""
    salon = str(demo["salon"]["id"])
    booking_id = str(booking["id"])
    before = await pool.fetchval("SELECT starts_at FROM bookings WHERE id = $1", booking_id)

    result = await actions.apply_form(
        pool, salon,
        {"action": "booking_reschedule", "booking_id": booking_id, "new_start": "мусор"},
        actor="owner:panel",
    )
    assert result["ok"] is False
    after = await pool.fetchval("SELECT starts_at FROM bookings WHERE id = $1", booking_id)
    assert after == before


async def test_client_save_requires_id_and_saves_flags(demo, pool) -> None:
    """Правка клиента: мусорный id — отказ, нормальный — флаги сохранены."""
    salon = str(demo["salon"]["id"])
    client_id = str(demo["client"]["id"])

    bad_id = await actions.apply_form(
        pool, salon,
        {"action": "client_save", "client_id": "abc", "notes": "х"},
        actor="owner:panel",
    )
    assert bad_id["ok"] is False
    assert bad_id["error"] == "bad_id"

    result = await actions.apply_form(
        pool, salon,
        {"action": "client_save", "client_id": client_id, "notes": "предоплата",
         "requires_prepayment": "on"},
        actor="owner:panel",
    )
    assert result["ok"] is True, result
    assert "предоплата включена" in result["message"]

    stored = await pool.fetchrow(
        "SELECT requires_prepayment, notes FROM clients WHERE id = $1", client_id
    )
    assert stored["requires_prepayment"] is True
    assert stored["notes"] == "предоплата"


async def test_client_save_reports_foreign_client(demo, pool) -> None:
    """Чужой клиент не редактируется: салон из формы игнорируется."""
    result = await actions.apply_form(
        pool, str(demo["salon"]["id"]),
        {"action": "client_save",
         "client_id": "11111111-1111-1111-1111-111111111111", "notes": "взлом"},
        actor="owner:panel",
    )
    assert result["ok"] is False
    # Код отказа важен: по нему панель показывает «не найден», а не 500.
    assert result["error"] in ("not_found", "save_failed")


async def test_hours_save_validates_weekday_and_time(demo, pool) -> None:
    """Расписание: неверный день, отсутствующее время и перевёрнутый интервал.

    Последнее важнее всего: мастер «работает с 20:00 до 10:00» — это не
    ночная смена, а опечатка, которая тихо убирает его с потока.
    """
    salon = str(demo["salon"]["id"])
    master_id = str(demo["master"]["id"])

    bad_weekday = await actions.apply_form(
        pool, salon,
        {"action": "hours_save", "master_id": master_id, "weekday": 9,
         "start": "10:00", "end": "18:00", "enabled": "1"},
        actor="owner:panel",
    )
    assert bad_weekday["error"] == "bad_weekday"

    no_time = await actions.apply_form(
        pool, salon,
        {"action": "hours_save", "master_id": master_id, "weekday": 1,
         "enabled": "1"},
        actor="owner:panel",
    )
    assert no_time["error"] == "bad_time"

    flipped = await actions.apply_form(
        pool, salon,
        {"action": "hours_save", "master_id": master_id, "weekday": 1,
         "start": "20:00", "end": "10:00", "enabled": "1"},
        actor="owner:panel",
    )
    assert flipped["error"] == "bad_time"
    assert "раньше конца" in flipped["message"]


async def test_hours_save_disables_day(demo, pool) -> None:
    """Выключенный день удаляется из графика, а не остаётся пустым интервалом.

    Пустая строка ``00:00–00:00`` прошла бы проверку ``start < end`` в
    ограничении БД и тихо вернула бы мастера в поток на «нулевом» часе.
    """
    salon = str(demo["salon"]["id"])
    master_id = str(demo["master"]["id"])
    assert await pool.fetchval(
        "SELECT count(*) FROM working_hours WHERE master_id = $1 AND weekday = 6",
        master_id,
    ) == 1, "в демо-салоне воскресенье рабочий"

    result = await actions.apply_form(
        pool, salon,
        {"action": "hours_save", "master_id": master_id, "weekday": 6},
        actor="owner:panel",
    )
    assert result["ok"] is True, result
    assert "нерабочий" in result["message"]

    assert await pool.fetchval(
        "SELECT count(*) FROM working_hours WHERE master_id = $1 AND weekday = 6",
        master_id,
    ) == 0
