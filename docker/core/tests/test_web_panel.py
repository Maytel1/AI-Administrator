"""Тесты веб-панели admin_core.web: рендер, форматирование, сессии, запросы.

Рендер проверяется без БД и без HTTP; запросы — на фикстуре ``demo``.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from admin_core.agent.agent import CLARIFY_REPLY, FALLBACK_REPLY, clean_reply
from admin_core.crm.internal import InternalCRM
from admin_core.web import dashboard, queries
from admin_core.web.actions import _decimal, _hhmm, _hhmm_text, apply_form
from admin_core.web.pages_core import page_bookings, page_today
from admin_core.web.pages_lists import (
    page_audit,
    page_clients,
    page_dialogs,
    page_masters,
    page_reports,
    page_services,
    page_settings,
)
from admin_core.web.render import (
    action_form,
    dt_local,
    esc,
    layout,
    login_page,
    money,
    page_head,
    table,
    tile,
)
from admin_core.web.render import (
    flash as flash_html,
)
from admin_core.web.roles import can_action
from admin_core.web.session import PageCtx, Panel, Session, check_builder_signatures

TZ = ZoneInfo("Asia/Yekaterinburg")
SALON = {"id": "11111111-1111-1111-1111-111111111111", "name": "Виктория",
         "plan": "trial", "crm_provider": "internal", "timezone": "Asia/Yekaterinburg"}
BOOKING = {
    "id": "b1",
    "service_title": "Маникюр", "duration_min": 120, "price": 2000, "is_active": True,
    "starts_at": datetime(2026, 10, 3, 6, 0, tzinfo=ZoneInfo("UTC")),  # 11:00 Тюмень
    "ends_at": datetime(2026, 10, 3, 8, 0, tzinfo=ZoneInfo("UTC")),
    "status": "booked", "source": "bot", "prepayment_status": "not_required",
    "client_name": "Иван", "client_phone": "+79001234567", "master_name": "Мария",
}
NBSP = "\u00a0"  # money() разделяет разряды и отбивает знак валюты неразрывным пробелом
STATS = {
    "total": 3, "confirmed": 1, "completed": 1, "cancelled": 0, "no_show": 0,
    "from_bot": 2, "revenue": 6700.0, "dialogs": 5, "new_clients": 1,
    "week": 12, "busy_masters": 1, "masters_total": 2,
}


def test_money_ru():
    assert money(1200) == f"1{NBSP}200{NBSP}₽"
    assert money(0) == f"0{NBSP}₽"
    assert money(None) == f"0{NBSP}₽"
    assert money("abc") == "—"


def test_esc_blocks_html():
    assert esc("<script>alert(1)</script>") == "&lt;script&gt;alert(1)&lt;/script&gt;"
    assert esc(None) == ""


def test_dt_local_converts_utc_to_salon_time():
    assert dt_local(datetime(2026, 10, 3, 6, 0, tzinfo=ZoneInfo("UTC")), TZ) == "11:00"
    assert dt_local(None, TZ) == "—"


def test_table_empty_shows_hint():
    assert "Пока пусто" in table(["Колонка"], [])
    assert "Пока пусто" not in table(["Колонка"], [["ячейка"]])


def test_layout_marks_active_section():
    """Активный раздел подсвечен, ссылки несут выбранный салон.

    Переключатель салона теперь рисует не layout, а page_head: держать его
    в layout было причиной того, что он всплывал рядом с любым блоком
    страницы. Проверяем оба места.
    """
    html = layout("Сегодня", "/today", page_head("Сегодня", "", SALON, [SALON]))
    assert '<a href="/today?salon=' in html
    assert 'class="active"' in html
    assert "<h1>Сегодня</h1>" in html
    # Переключатель с именем салона — в шапке, а не болтается рядом.
    assert "Виктория" in html
    assert 'class="salon-switch"' in html


def test_login_page_has_password_field():
    html = login_page("Неверный пароль")
    assert 'name="password"' in html
    assert "Неверный пароль" in html


def test_page_today_shows_kpi_and_schedule():
    html = page_today(dict(STATS), [dict(BOOKING)], TZ, SALON, [SALON])
    assert f"6{NBSP}700{NBSP}₽" in html  # выручка дня
    assert "Иван" in html and "Мария" in html    # расписание
    assert "Мастера в работе" in html
    assert "50%" in html                         # 1 из 2 мастеров


def test_page_today_empty_schedule_has_hint():
    html = page_today({**STATS, "total": 0, "revenue": 0}, [], TZ, SALON, [SALON])
    assert "На сегодня записей нет" in html


def test_page_bookings_filters_and_escape():
    html = page_bookings([dict(BOOKING)], TZ, "booked", SALON, [SALON])
    assert "Найдено: 1" in html
    assert "Маникюр" in html
    assert 'status=cancelled' in html  # ссылки-фильтры по статусам
    assert "Без имени" in page_bookings(
        [{**BOOKING, "client_name": None}], TZ, "", SALON, [SALON]
    )


def test_page_clients_shows_prepayment_flag():
    html = page_clients(
        [{"id": "c1", "name": "Иван", "phone": "+79001234567", "completed_visits": 2,
          "last_visit": datetime(2026, 9, 1, tzinfo=ZoneInfo("UTC")), "upcoming": 1,
          "requires_prepayment": True, "notes": ""}],
        TZ, SALON, [SALON], PageCtx(csrf="f" * 32, role="owner"),
    )
    assert "+79001234567" in html
    assert "требует" in html  # чекбокс предоплаты виден владельцу


def test_page_services_and_masters():
    svc = page_services(
        [{"id": "s1", "title": "Маникюр", "duration_min": 120, "price": 2000, "is_active": True}],
        SALON, [SALON],
    )
    assert f"2{NBSP}000{NBSP}₽" in svc and "120 мин" in svc
    masters = page_masters(
        [{"id": "m1", "name": "Мария", "is_active": True, "services_count": 2,
          "hours": '[{"weekday": 2, "start": "10:00:00", "end": "20:00:00"}]'}],
        SALON, [SALON],
    )
    assert "Ср 10:00–20:00" in masters


def test_page_dialogs_renders_transcript():
    rows = [{"id": "d1", "channel": "telegram",
             "started_at": datetime(2026, 10, 3, 6, 0, tzinfo=ZoneInfo("UTC")),
             "client_name": "Иван", "client_phone": "+79", "messages": 2,
             "outcome": "booked"}]
    messages = [{"role": "user", "content": "Запись на завтра",
                 "created_at": datetime(2026, 10, 3, 6, 0, tzinfo=ZoneInfo("UTC"))}]
    html = page_dialogs(rows, messages, TZ, SALON, [SALON])
    assert "Telegram" in html
    assert "Клиент" in html
    assert "Запись на завтра" in html


def test_page_audit_renders_json_details():
    html = page_audit(
        [{"ts": datetime(2026, 10, 3, 6, 0, tzinfo=ZoneInfo("UTC")), "actor": "bot",
          "action": "create_booking", "details": '{"booking_id": "x"}'}],
        TZ, SALON, [SALON],
    )
    assert "create_booking" in html
    assert "booking_id" in html


def test_page_settings_shows_plan_and_roadmap():
    html = page_settings(
        SALON, [SALON], {"LLM_PROVIDER": "yandexgpt"},
        session={"login": "boss@test.ru", "role": "owner"},
        team=[{"id": "u1", "login": "boss@test.ru", "name": "Иван", "role": "owner",
               "totp_enabled": True, "last_login_at": None}],
        ctx=PageCtx(csrf="f" * 32, role="owner"),
    )
    assert "Пробный" in html
    assert "yandexgpt" in html
    assert "Владелец точки" in html  # кто вошёл и его роль
    assert "цены и услуги" in html  # права роли показаны
    assert "Команда точки" in html  # список команды
    assert "2FA включена" in html
    assert "MVP 0.3" in html


def test_tile_escapes_value():
    assert "<script>" not in tile("<script>", "плитка")
def test_parse_hhmm():
    assert _hhmm({"start": "9:5"}, "start") == time(9, 5)
    assert _hhmm({"start": "23:59"}, "start") == time(23, 59)
    assert _hhmm({"start": ""}, "start") is None
    assert _hhmm({"start": "abc"}, "start") is None
    assert _hhmm({"start": "25:00"}, "start") is None
    assert _hhmm_text(time(9, 5)) == "09:05"
    assert _hhmm_text(None) == "—"


def test_parse_decimal_accepts_russian_format():
    assert _decimal("2000") == 2000.0
    assert _decimal("2 000") == 2000.0
    assert _decimal("2 000,50") == 2000.5
    assert _decimal("abc") is None
    assert _decimal(None) is None


def _session(panel: Panel, role: str = "owner", login: str = "boss@test.ru") -> str:
    """Тестовая сессия напрямую: аварийный вход по env закрыт по коду."""
    token = "test-token-" + login
    panel.sessions[token] = Session(
        user={"login": login, "role": role}, role=role, login=login, salon_id=None,
        until=datetime.now(ZoneInfo("UTC")) + timedelta(hours=1),
    )
    return token


def test_csrf_token_is_stable_and_bound_to_session():
    panel = Panel(pool=None, password="")
    token = _session(panel)
    first = panel.csrf_token(token)
    assert first == panel.csrf_token(token)          # стабилен в рамках сессии
    assert panel.csrf_ok(token, first) is True
    assert panel.csrf_ok(token, "0" * 32) is False   # чужой токен не проходит
    assert panel.csrf_ok(token, "") is False
    other = _session(panel, login="second@test.ru")
    assert panel.csrf_token(other) != first          # привязан к конкретной сессии
    assert panel.csrf_ok(other, first) is False


def test_action_form_contains_csrf_and_action():
    html = action_form("service_save", "a" * 32, "/services", '<input name="price">')
    assert 'action="/actions"' in html
    assert 'value="service_save"' in html
    assert f'value="{"a" * 32}"' in html
    assert 'name="back" value="/services"' in html


def test_flash_colors():
    assert 'class="flash ok"' in flash_html("Готово")
    assert 'class="flash err"' in flash_html("Ошибка", True)
    assert flash_html("") == ""


async def test_panel_login_and_session_lifetime():
    panel = Panel(pool=None, password="")
    token = _session(panel)
    assert await panel.authorized(token)
    assert not await panel.authorized("чужая-сессия")
    assert not await panel.authorized("")
    await panel.logout(token)
    assert not await panel.authorized(token)


async def test_panel_expired_session_is_dropped():
    panel = Panel(pool=None, password="")
    token = _session(panel)
    session = panel.sessions[token]
    session.until = datetime.now(ZoneInfo("UTC")) - timedelta(seconds=1)
    assert not await panel.authorized(token)
    assert token not in panel.sessions


def test_emergency_password_login_is_gone():
    """Аварийный вход по общему паролю удалён — он давал роль team (все точки).

    Общий пароль из env не должен обходить учётные записи ни при каких условиях.
    """
    panel = Panel(pool=None, password="env-password")
    assert not hasattr(panel, "login"), "метод Panel.login() должен быть удалён"
    assert panel.password == "", "общий пароль не хранится и не используется"


def test_day_bounds_is_exactly_24h_in_utc():
    start, end = queries.day_bounds(TZ)
    assert (end - start) == timedelta(hours=24)
    assert start.tzinfo == queries.UTC


async def test_queries_read_real_data(demo, pool):
    """Панель читает те же данные, что и бот: прайс, мастера, клиенты."""
    salon_id = str(demo["salon"]["id"])
    services = await queries.services_list(pool, salon_id)
    assert any(s["title"] == "Тест стрижка" for s in services)
    masters = await queries.masters_list(pool, salon_id)
    assert masters[0]["name"] == "Тест Мастер"
    clients = await queries.clients_list(pool, salon_id)
    assert clients[0]["phone"].startswith("+7900")
    assert await queries.dialogs_list(pool, salon_id) == []
    assert await queries.audit_list(pool, salon_id) is not None


async def test_dashboard_counts_booking_of_today(demo, pool):
    """Запись на сегодня попадает и в плитки, и в расписание дня."""
    salon, client = demo["salon"], demo["client"]
    tz = ZoneInfo(salon["timezone"])
    now = datetime.now(tz)
    target = now.replace(hour=min(now.hour + 1, 22), minute=0, second=0, microsecond=0)
    await pool.execute(
        "INSERT INTO bookings (salon_id, client_id, master_id, service_id, starts_at, ends_at) "
        "VALUES ($1,$2,$3,$4, $5, $6)",
        salon["id"], client["id"], demo["master"]["id"], demo["service"]["id"],
        target.astimezone(ZoneInfo("UTC")),
        (target + timedelta(hours=1)).astimezone(ZoneInfo("UTC")),
    )
    start, end = queries.day_bounds(tz)
    stats = await dashboard.today_stats(pool, str(salon["id"]), start, end)
    assert stats["total"] == 1
    assert stats["revenue"] == 1000.0
    schedule = await dashboard.schedule_for_day(pool, str(salon["id"]), start, end)
    assert schedule[0]["client_name"] == "Тест Клиент"
    assert schedule[0]["master_name"] == "Тест Мастер"


async def test_queries_isolated_by_salon(demo, pool):
    """Изоляция tenant: чужой салон не видит данных (WHERE salon_id = $1)."""
    services = await queries.services_list(pool, "00000000-0000-0000-0000-000000000000")
    assert services == []


async def test_action_validates_before_touching_db(demo, pool):
    """Мусор в форме не доходит до БД — возвращаем понятную ошибку."""
    salon_id = str(demo["salon"]["id"])
    svc = await queries.services_list(pool, salon_id)
    service_id = str(svc[0]["id"])

    bad_price = await apply_form(pool, salon_id, {
        "action": "service_save", "service_id": service_id,
        "price": "миллион", "duration_min": "60", "is_active": "on",
    })
    assert bad_price["ok"] is False
    assert "Цена" in bad_price["message"]

    bad_duration = await apply_form(pool, salon_id, {
        "action": "service_save", "service_id": service_id,
        "price": "1000", "duration_min": "2",
    })
    assert bad_duration["ok"] is False
    assert bad_duration["error"] == "bad_duration"

    bad_status = await apply_form(pool, salon_id, {
        "action": "booking_status",
        "booking_id": "00000000-0000-0000-0000-000000000000",
        "status": "hacked",
    })
    assert bad_status["ok"] is False and bad_status["error"] == "bad_status"

    unknown = await apply_form(pool, salon_id, {"action": "drop_table"})
    assert unknown["ok"] is False

    after = await queries.services_list(pool, salon_id)
    assert after[0]["price"] == svc[0]["price"]  # цена не изменилась


async def test_action_cannot_edit_other_salon(demo, pool):
    """Заявка с чужим salon_id не трогает данные нашей точки."""
    ours = str(demo["salon"]["id"])
    theirs = "00000000-0000-0000-0000-000000000000"
    svc = await queries.services_list(pool, ours)
    service_id = str(svc[0]["id"])
    result = await apply_form(pool, theirs, {
        "action": "service_save", "service_id": service_id,
        "price": "1", "duration_min": "10", "is_active": "on",
    })
    assert result["ok"] is False and result["error"] == "not_found"
    unchanged = await queries.services_list(pool, ours)
    assert unchanged[0]["price"] == svc[0]["price"]


async def test_service_price_change_writes_audit(demo, pool):
    """Правка цены меняет данные и попадает в журнал аудита."""
    salon_id = str(demo["salon"]["id"])
    svc = await queries.services_list(pool, salon_id)
    service_id = str(svc[0]["id"])
    result = await apply_form(pool, salon_id, {
        "action": "service_save", "service_id": service_id,
        "price": "2500", "duration_min": "90", "is_active": "on",
    })
    assert result["ok"] is True
    updated = await queries.services_list(pool, salon_id)
    assert float(updated[0]["price"]) == 2500.0
    assert updated[0]["duration_min"] == 90
    audit = await pool.fetchval(
        "SELECT count(*) FROM audit_log WHERE salon_id=$1 AND action='update_service'",
        demo["salon"]["id"],
    )
    assert audit == 1


async def test_client_prepayment_and_notes_saved(demo, pool):
    """Флаг предоплаты и заметки — то, что бот читает в диалоге."""
    salon_id = str(demo["salon"]["id"])
    client_id = str(demo["client"]["id"])
    result = await apply_form(pool, salon_id, {
        "action": "client_save", "client_id": client_id,
        "requires_prepayment": "on", "notes": "аллергия на латекс",
    })
    assert result["ok"] is True
    row = await pool.fetchrow(
        "SELECT requires_prepayment, notes FROM clients WHERE id = $1", client_id
    )
    assert row["requires_prepayment"] is True
    assert row["notes"] == "аллергия на латекс"


async def test_booking_status_change_and_slot_release(demo, pool):
    """Отмена освобождает слот: exclusion-констрейнт больше не мешает."""
    crm = InternalCRM(pool)
    salon_id, client_id = str(demo["salon"]["id"]), str(demo["client"]["id"])
    created = await crm.create_booking(
        salon_id, client_id, "Тест стрижка", time_text="11:00", created_by="test"
    )
    assert created["ok"] is True
    result = await apply_form(pool, salon_id, {
        "action": "booking_status", "booking_id": created["booking_id"], "status": "cancelled",
    })
    assert result["ok"] is True
    status = await pool.fetchval(
        "SELECT status FROM bookings WHERE id = $1", created["booking_id"]
    )
    assert status == "cancelled"


async def test_hours_save_and_disable(demo, pool):
    """График мастера сохраняется и отключается — слоты следуют за ним."""
    salon_id, master_id = str(demo["salon"]["id"]), str(demo["master"]["id"])
    saved = await apply_form(pool, salon_id, {
        "action": "hours_save", "master_id": master_id, "weekday": "0",
        "enabled": "on", "start": "11:00", "end": "15:00",
    })
    assert saved["ok"] is True
    row = await pool.fetchrow(
        "SELECT start_time::text, end_time::text FROM working_hours "
        "WHERE master_id = $1 AND weekday = 0",
        master_id,
    )
    assert row["start_time"].startswith("11:00")
    off = await apply_form(pool, salon_id, {
        "action": "hours_save", "master_id": master_id, "weekday": "0",
    })
    assert off["ok"] is True
    assert await pool.fetchval(
        "SELECT count(*) FROM working_hours WHERE master_id = $1 AND weekday = 0", master_id
    ) == 0


async def test_hours_rejects_bad_window(demo, pool):
    salon_id, master_id = str(demo["salon"]["id"]), str(demo["master"]["id"])
    bad = await apply_form(pool, salon_id, {
        "action": "hours_save", "master_id": master_id, "weekday": "0",
        "enabled": "on", "start": "18:00", "end": "10:00",
    })
    assert bad["ok"] is False and bad["error"] == "bad_time"
    bad_day = await apply_form(pool, salon_id, {
        "action": "hours_save", "master_id": master_id, "weekday": "9",
        "enabled": "on", "start": "10:00", "end": "18:00",
    })
    assert bad_day["ok"] is False and bad_day["error"] == "bad_weekday"


def test_pages_carry_csrf_token_into_forms():
    """Формы правки несут CSRF-токен сессии — иначе браузер их не отправит."""
    panel = Panel(pool=None, password="")
    token = panel.csrf_token(_session(panel))
    ctx = PageCtx(csrf=token, role="owner", login="boss@test.ru")
    services = page_services(
        [{"id": "s1", "title": "Маникюр", "duration_min": 120, "price": 2000, "is_active": True}],
        SALON, [SALON], ctx,
    )
    assert 'value="service_save"' in services
    assert token in services
    clients = page_clients(
        [{"id": "c1", "name": "Иван", "phone": "+79", "completed_visits": 1,
          "last_visit": None, "upcoming": 0, "requires_prepayment": False, "notes": ""}],
        TZ, SALON, [SALON], ctx,
    )
    assert 'value="client_save"' in clients and token in clients
    masters = page_masters(
        [{"id": "m1", "name": "Мария", "is_active": True, "services_count": 1,
          "hours": '[{"weekday": 1, "start": "10:00:00", "end": "18:00:00"}]'}],
        SALON, [SALON], ctx,
    )
    assert 'value="hours_save"' in masters
    assert masters.count('name="weekday"') == 7  # все дни недели редактируются
    assert token in masters


async def test_owner_can_create_staff_from_panel(demo, pool):
    """Владелец создаёт сотрудника прямо в панели и получает пароль один раз."""
    from admin_core.web import users

    salon_id = str(demo["salon"]["id"])
    result = await apply_form(
        pool, salon_id,
        {"action": "user_create", "login": "novichok@test.ru", "name": "Пётр", "role": "staff"},
        actor="owner:boss@test.ru",
    )
    assert result["ok"] is True
    assert "novichok@test.ru" in result["message"]
    once = result["once"]
    assert len(once["password"]) == 16  # временный пароль сгенерирован
    # и этот сотрудник реально может войти с выданным паролем
    check = await users.authenticate(pool, "novichok@test.ru", once["password"])
    assert check["ok"] is True and check["user"]["role"] == "staff"


async def test_panel_cannot_issue_team_role(demo, pool):
    """Роль team из панели не выдаётся — иначе владелец получил бы все точки."""
    result = await apply_form(
        pool, str(demo["salon"]["id"]),
        {"action": "user_create", "login": "sneaky@test.ru", "role": "team"},
        actor="owner:boss@test.ru",
    )
    assert result["ok"] is False
    assert result["error"] == "bad_role"
    assert await pool.fetchval(
        "SELECT count(*) FROM panel_users WHERE login = 'sneaky@test.ru'"
    ) == 0


async def test_user_totp_returns_secret_once(demo, pool):
    """Включение 2FA возвращает секрет и otpauth-ссылку — ровно один раз."""
    from admin_core.web.auth import totp_code, verify_totp

    salon_id = str(demo["salon"]["id"])
    await apply_form(
        pool, salon_id,
        {"action": "user_create", "login": "code@test.ru", "role": "staff"},
        actor="owner:boss@test.ru",
    )
    result = await apply_form(
        pool, salon_id, {"action": "user_totp", "login": "code@test.ru"},
        actor="owner:boss@test.ru",
    )
    assert result["ok"] is True
    secret = result["once"]["secret"]
    assert result["once"]["uri"].startswith("otpauth://totp/")
    assert await pool.fetchval(
        "SELECT totp_enabled FROM panel_users WHERE login = 'code@test.ru'"
    ) is True
    assert verify_totp(secret, totp_code(secret)) is True


async def test_cannot_disable_last_owner(demo, pool):
    """Последнего владельца точки отключить нельзя — иначе точка останется без доступа."""
    from admin_core.web import users

    salon_id = str(demo["salon"]["id"])
    created = await users.create_user(
        pool, login="last@test.ru", password="parol-12345", role="owner", salon_id=salon_id
    )
    blocked = await users.disable_user(
        pool, user_id=created["id"], actor="owner:x@test.ru", salon_id=salon_id
    )
    assert blocked["ok"] is False and blocked["error"] == "last_owner"
    assert await pool.fetchval(
        "SELECT is_active FROM panel_users WHERE id = $1", created["id"]
    ) is True


async def test_disable_second_owner_works(demo, pool):
    from admin_core.web import users

    salon_id = str(demo["salon"]["id"])
    first = await users.create_user(
        pool, login="first@test.ru", password="parol-12345", role="owner", salon_id=salon_id
    )
    await users.create_user(
        pool, login="second@test.ru", password="parol-12345", role="owner", salon_id=salon_id
    )
    result = await users.disable_user(
        pool, user_id=first["id"], actor="owner:x@test.ru", salon_id=salon_id
    )
    assert result["ok"] is True
    assert await pool.fetchval(
        "SELECT is_active FROM panel_users WHERE id = $1", first["id"]
    ) is False


def test_settings_page_shows_team_management():
    """Владелец видит форму создания и кнопки 2FA/отключения, администратор — нет."""
    team = [{"id": "u1", "login": "staff@test.ru", "name": "Пётр", "role": "staff",
             "totp_enabled": False, "last_login_at": None}]
    owner_html = page_settings(
        SALON, [SALON], {}, session={"login": "boss@test.ru", "role": "owner"},
        team=team, ctx=OWNER_CTX,
    )
    assert 'value="user_create"' in owner_html
    assert 'value="user_totp"' in owner_html
    assert 'value="user_disable"' in owner_html
    staff_html = page_settings(
        SALON, [SALON], {}, session={"login": "p@test.ru", "role": "staff"},
        team=team, ctx=STAFF_CTX,
    )
    assert 'value="user_create"' not in staff_html  # администратор команду не ведёт
    assert "Пётр" in staff_html  # но состав команды видит


def test_settings_page_shows_one_time_secret_once():
    html = page_settings(
        SALON, [SALON], {}, session={"login": "boss@test.ru", "role": "owner"},
        team=[], ctx=OWNER_CTX,
        once=[{"login": "novichok@test.ru", "password": "Xk39dPq7mZa4LbT2",
               "note": "Пароль показывается один раз"}],
    )
    assert "Xk39dPq7mZa4LbT2" in html
    assert "один раз" in html
    without = page_settings(
        SALON, [SALON], {}, session={"login": "boss@test.ru", "role": "owner"},
        team=[], ctx=OWNER_CTX, once=[],
    )
    assert "Xk39dPq7mZa4LbT2" not in without  # второй раз не показываем


def test_once_secrets_are_not_in_url():
    """Пароль не должен попадать в адресную строку (история браузера, логи)."""
    panel = Panel(pool=None, password="")
    token = _session(panel)
    panel.push_once(token, {"password": "Sekret123"})
    assert panel.take_once(token)[0]["password"] == "Sekret123"
    assert panel.take_once(token) == []  # второй раз пусто
    other = _session(panel, login="other@test.ru")
    assert panel.take_once(other) == []  # чужая сессия не видит чужих секретов


def test_once_secrets_expire():
    panel = Panel(pool=None, password="")
    token = _session(panel)
    panel.push_once(token, {"password": "Old"})
    panel.once[token][0]["at"] = datetime.now(ZoneInfo("UTC")) - timedelta(minutes=11)
    panel.purge_expired_once()
    assert panel.take_once(token) == []


OWNER_CTX = PageCtx(csrf="f" * 32, role="owner", login="boss@test.ru")
STAFF_CTX = PageCtx(csrf="s" * 32, role="staff", login="recep@test.ru")


def test_pages_render_edit_forms_only_with_permission():
    """Права видно и в интерфейсе: у роли без права кнопок правки нет."""
    services = [
        {"id": "s1", "title": "Маникюр", "duration_min": 120, "price": 2000, "is_active": True}
    ]
    assert 'value="service_save"' in page_services(services, SALON, [SALON], OWNER_CTX)
    assert 'value="service_save"' not in page_services(services, SALON, [SALON], STAFF_CTX)
    clients = [
        {"id": "c1", "name": "Иван", "phone": "+79", "completed_visits": 1,
         "last_visit": None, "upcoming": 0, "requires_prepayment": False, "notes": ""},
    ]
    assert 'value="client_save"' in page_clients(clients, TZ, SALON, [SALON], OWNER_CTX)
    masters = [
        {"id": "m1", "name": "Мария", "is_active": True, "services_count": 1,
         "hours": '[{"weekday": 1, "start": "10:00:00", "end": "18:00:00"}]'},
    ]
    owner_masters = page_masters(masters, SALON, [SALON], OWNER_CTX)
    assert owner_masters.count('name="weekday"') == 7  # 7 дней редактируются
    staff_masters = page_masters(masters, SALON, [SALON], STAFF_CTX)
    assert 'name="weekday"' not in staff_masters  # расписание администратор не правит
    assert "Вт 10:00–18:00" in staff_masters  # но график видит (weekday 1 = вторник)


def test_action_menu_hidden_without_permission():
    """Меню действий есть только у роли с правом и при реальном csrf."""
    def html(ctx):
        return page_bookings([dict(BOOKING)], TZ, "", SALON, [SALON], ctx)

    # Без csrf и у гостя кнопок нет: пункт «Действия» без права — это
    # приглашение нажать, после которого ничего не происходит.
    assert "menu-pop" not in html(None)
    assert "menu-pop" not in html(PageCtx(csrf="g" * 32, role="guest"))
    owner_html = html(OWNER_CTX)
    assert "menu-pop" in owner_html
    assert "Выполнена" in owner_html and "Отменена" in owner_html
    # Перенос открывает второе мини-окно: дата и время, а не кнопка «куда?».
    assert "booking_reschedule" in owner_html and "new_start" in owner_html
    staff_html = html(STAFF_CTX)
    assert "menu-pop" in staff_html  # у администратора это как раз его рутина


def test_closed_booking_offers_no_actions():
    """Выполненная и отменённая запись закрыта: меню действий не предлагается."""
    for closed in ("completed", "cancelled"):
        row = {**BOOKING, "status": closed}
        html = page_bookings([row], TZ, "", SALON, [SALON], OWNER_CTX)
        assert "menu-pop" not in html, closed
        assert "закрыта" in html, closed


def test_period_picker_shows_chosen_window():
    """Подпись периода соответствует выбранному окну, а не всегда «Месяц»."""
    week = page_bookings([dict(BOOKING)], TZ, "", SALON, [SALON], OWNER_CTX, days=7)
    assert "Период: Неделя" in week
    assert "Период: Месяц" not in week
    # Ссылка сохраняет выбранный статус: иначе смена периода сбрасывала фильтр.
    assert "days=90&amp;status=confirmed" in page_bookings(
        [dict(BOOKING)], TZ, "confirmed", SALON, [SALON], OWNER_CTX
    )


def test_bookings_table_has_no_stray_cells():
    """В строке ровно столько ячеек, сколько заголовков.

    Раньше строка выдавала ещё две ячейки — отметку предоплаты и форму
    переноса, — на которые в шапке не было ни заголовка. Таблица от этого
    разъезжалась, а перенос дублировал пункт «Действия».
    """
    html = page_bookings([dict(BOOKING)], TZ, "", SALON, [SALON], OWNER_CTX)
    header_part, body_part = html.split("</thead>", 1)
    headers = header_part.count("<th>")
    first_row = body_part.split("<tr>", 1)[1].split("</tr>", 1)[0]
    assert first_row.count("<td>") == headers


def test_filters_are_in_table_header():
    """Фильтры и выбор периода живут в шапке над колонками, а не отдельной полосой."""
    html = page_bookings([dict(BOOKING)], TZ, "", SALON, [SALON], OWNER_CTX)
    panel_head = html.split('class="panel-head"', 1)[1]
    # Обе кнопки периода и статусных фильтров — внутри блока шапки.
    assert 'class="picker"' in panel_head.split("</div></div>")[0]
    assert "Период:" in panel_head
    assert "Найдено: 1" in panel_head


def test_today_schedule_uses_action_menu():
    """В расписании на сегодня — то же меню действий, что и в «Записях».

    Здесь была та же болезнь: строка выдавала больше ячеек, чем заголовков,
    и отдельные поля с датой и кнопкой «Перенести» дублировали меню.
    """
    row = {**BOOKING, "status": "confirmed"}
    html = page_today(dict(STATS), [row], TZ, SALON, [SALON], OWNER_CTX)
    head, _, body = html.partition("</thead>")
    first_row = body.split("<tr>", 1)[1].split("</tr>", 1)[0]
    assert first_row.count("<td>") == head.count("<th>")
    assert "menu-pop" in html and "booking_reschedule" in html
    # Отдельных форм переноса в таблице быть не должно.
    assert html.count('name="new_start"') == html.count("menu-pop")


def test_reports_and_settings_scroll_inside():
    """Отчёты и настройки не прокручиваются как страницу: шапка стоит, тело ездит."""
    reports = page_reports(
        {
            "funnel": {
                "revenue": 1000.0, "bookings": 4, "dialogs": 9, "conversion": 44,
                "avg_check": 250.0, "new_clients": 1, "llm_cost": 10.0,
            },
            "loss": {"lost_rate": 5, "total": 4, "cancelled": 1, "no_show": 0},
            "load": [
                {"name": "Мария", "bookings": 2, "busy_hours": 3.5, "revenue": 500.0},
            ],
            "by_day": [], "bot": {},
        },
        SALON, [SALON], ctx=OWNER_CTX,
    )
    assert '<main class="inner">' in reports
    assert 'class="page-head"' in reports and 'class="page-body"' in reports
    settings = page_settings(SALON, [SALON], {"LLM_PROVIDER": "stub"})
    assert '<main class="inner">' in settings
    assert 'class="page-body"' in settings


def test_services_are_list_not_card_grid():
    """Прайс — ровный список позиций, а не сетка карточек.

    В сетке поля «цена/длительность» сжимались до нечитаемых 120px,
    и текст рядом с услугой рвался.
    """
    html = page_services(
        [{"id": "s1", "title": "Маникюр", "duration_min": 120,
          "price": 2000, "is_active": True}],
        SALON, [SALON], OWNER_CTX,
    )
    assert "grid-cards" not in html
    assert 'class="list-body"' in html
    assert "item-head" in html


def test_today_blocks_order():
    """Порядок на «Сегодня»: заголовок, загрузка, плитки, расписание вплотную.

    Полоса загрузки уехала из-под таблицы под заголовок, плитки стали одним
    рядом, а расписание прижато к ним. Сводку «подтверждено N, выполнено N»
    убрали — она дублировала плитки и съедала высоту экрана.
    """
    html = page_today(dict(STATS), [dict(BOOKING)], TZ, SALON, [SALON], OWNER_CTX)
    assert 'class="load-line"' in html
    assert 'class="tiles tiles-row"' in html
    # Отдельной карточки «Загрузка» больше нет.
    assert "<h2>Загрузка</h2>" not in html
    # Сводка-дубль убрана, и расписание идёт сразу за плитками.
    assert "подтверждено" not in html
    assert "panel-tight" in html
    i_load = html.index('class="load-line"')
    i_tiles = html.index('tiles tiles-row')
    i_panel = html.index("panel-tight")
    i_table = html.index("<table>")
    assert i_load < i_tiles < i_panel < i_table, "блоки идут не в том порядке"


def test_today_dashboard_two_columns():
    """Шапка «Сегодня» двумя колонками: слева заголовок, загрузка и салон.

    Переключатель салона раньше висел справа от всего содержимого (его
    рисовал layout), из-за чего шапка разъезжалась. Теперь он внутри
    левой колонки, а плитки стоят рядом с ним.
    """
    html = page_today(dict(STATS), [dict(BOOKING)], TZ, SALON, [SALON], OWNER_CTX)
    assert 'class="dash-top"' in html and 'class="dash-side"' in html
    assert 'class="salon-switch"' in html
    # Переключатель стоит внутри левой колонки, до плиток. Считаем по
    # индексам: вложенные div внутри колонки не дают разбирать её по </div>.
    i_side = html.index('class="dash-side"')
    i_switch = html.index("salon-switch")
    i_tiles = html.index("tiles tiles-row")
    assert i_side < i_switch < i_tiles
    # И ровно один переключатель на страницу — дубль был бы некрасив.
    assert html.count("salon-switch") == 1


def test_layout_wraps_content_in_own_block():
    """Контент лежит в своей обёртке — иначе он становится детьми flex-строки.

    .top объявлен как flex, и без обёртки каждый блок страницы становился его
    ребёнком: плитки и расписание вставали в один ряд вместо того, чтобы
    идти сверху вниз. Обёртка обязана быть на каждой странице, поэтому
    проверяем и обычную схему, и внутреннюю прокрутку.
    """
    today = page_today(dict(STATS), [dict(BOOKING)], TZ, SALON, [SALON], OWNER_CTX)
    assert '<div class="top"><div class="content">' in today
    # Шапка и расписание — внутри обёртки, то есть один поток, не два столбца.
    inner = today.split('<div class="content">', 1)[1]
    assert inner.index("dash-top") < inner.index("panel-tight")
    # На странице с внутренней прокруткой обёртка тоже на месте.
    reports = page_reports(
        {
            "funnel": {
                "revenue": 1000.0, "bookings": 4, "dialogs": 9, "conversion": 44,
                "avg_check": 250.0, "new_clients": 1, "llm_cost": 10.0,
            },
            "loss": {"lost_rate": 5, "total": 4, "cancelled": 1, "no_show": 0},
            "load": [], "by_day": [], "bot": {},
        },
        SALON, [SALON], ctx=OWNER_CTX,
    )
    assert '<div class="content">' in reports


def test_every_tab_uses_shared_header():
    """Все вкладки рисуют шапку одним компонентом page_head.

    Заголовок и переключатель салона раньше каждый раз писались вручную, а
    layout рисовал переключатель рядом со всем содержимым — из-за чего вкладки
    выглядели по-разному, а селект уезжал вбок. Теперь шапка одна.
    """
    clients = page_clients([], TZ, SALON, [SALON], OWNER_CTX)
    services = page_services(
        [{"id": "s1", "title": "Маникюр", "duration_min": 120,
          "price": 2000, "is_active": True}],
        SALON, [SALON], OWNER_CTX,
    )
    for html in (clients, services):
        assert '<div class="page-head">' in html
        assert 'class="salon-switch"' in html
        # Переключатель внутри шапки, а не отдельным блоком рядом.
        assert html.index("page-head") < html.index("salon-switch")
        assert html.count('class="salon-switch"') == 1


def test_flash_shown_after_action():
    html = page_services(
        [{"id": "s1", "title": "Маникюр", "duration_min": 120, "price": 2000, "is_active": True}],
        SALON, [SALON], OWNER_CTX, flash="Цена сохранена",
    )
    assert "Цена сохранена" in html and 'class="flash ok"' in html
    err = page_services(
        [{"id": "s1", "title": "Маникюр", "duration_min": 120, "price": 2000, "is_active": True}],
        SALON, [SALON], flash="Цена не сохранена", flash_error=True,
    )
    assert 'class="flash err"' in err


async def test_get_free_slots_without_service_asks_back(demo, pool):
    """Модель позвала инструмент без услуги — ждём переспрос, а не пустой список."""
    from admin_core.agent.tools import build_dispatch

    dispatch = build_dispatch(
        InternalCRM(pool), str(demo["salon"]["id"]), str(demo["client"]["id"])
    )
    result = await dispatch("get_free_slots", {"service": "  "})
    assert result["error"] == "service_required"
    assert "Тест стрижка" in result["services"]
    ok = await dispatch("get_free_slots", {"service": "Тест стрижка"})
    assert "slots" in ok


def test_clean_reply_hides_tool_calls_and_empty():
    """Клиент не должен видеть сырой JSON вызова инструмента."""
    assert clean_reply(None) == FALLBACK_REPLY
    assert clean_reply("   ") == FALLBACK_REPLY
    junk = '{"type": "function", "function": {"name": "get_free_slots"}}'
    assert "function" not in clean_reply(junk)
    assert clean_reply("Здравствуйте! Чем помочь?") == "Здравствуйте! Чем помочь?"


async def test_agent_stops_on_repeated_tool_calls():
    """Модель повторяет один вызов — агент перестаёт крутиться и уточняет у клиента."""
    from admin_core.agent.agent import AgentSession
    from admin_core.llm.base import LLMResponse, ToolCall

    class LoopProvider:
        """На каждый запрос возвращает тот же вызов инструмента — иначе не сойдёмся."""

        name = "loop"
        calls = 0

        async def chat(self, _messages, tools=None):  # noqa: ANN001, ARG002
            type(self).calls += 1
            return LLMResponse(
                tool_calls=[ToolCall(name="get_free_slots", arguments={"service": ""})]
            )

    session = AgentSession(
        crm=object(), provider=LoopProvider(),
        salon={"id": "s", "name": "Салон"}, client={"id": "c", "name": "Иван"},
    )
    reply = await session.run_turn("Какие слоты в понедельник?")
    assert reply == CLARIFY_REPLY
    assert LoopProvider.calls == 2  # второй одинаковый вызов — и стоп


async def test_authenticate_login_password_and_totp(demo, pool):
    """Вход: верный пароль — ок; без кода 2FA — просим код; с кодом — ок."""
    from admin_core.web import users
    from admin_core.web.auth import generate_totp_secret, totp_code

    secret = generate_totp_secret()
    unique = str(demo["salon"]["id"])[:8]
    created = await users.create_user(
        pool, login=f"boss-{unique}@test.ru", password="parol-12345",
        role="owner", salon_id=str(demo["salon"]["id"]), name="Иван",
        totp_secret=secret, totp_enabled=True,
    )
    assert created["ok"] is True
    login = created["login"]

    wrong = await users.authenticate(pool, login, "неправильный")
    assert wrong["ok"] is False and wrong["error"] == "invalid"

    no_code = await users.authenticate(pool, login, "parol-12345")
    assert no_code["ok"] is False and no_code["error"] == "need_totp"

    bad_code = await users.authenticate(pool, login, "parol-12345", "000000")
    assert bad_code["ok"] is False and bad_code["error"] == "need_totp"

    ok = await users.authenticate(pool, login, "parol-12345", totp_code(secret))
    assert ok["ok"] is True
    assert ok["user"]["role"] == "owner"
    assert ok["user"]["id"] == ok["user"]["id"]  # id — строка, а не UUID-объект
    assert isinstance(ok["user"]["id"], str)
    assert "password_hash" not in ok["user"] and "totp_secret" not in ok["user"]
    last_login = await pool.fetchval(
        "SELECT last_login_at FROM panel_users WHERE login = $1", login
    )
    assert last_login is not None


async def test_authenticate_rejects_unknown_login_identically(demo, pool):
    """Неизвестный логин и неверный пароль дают одну ошибку — нельзя перебором узнать, кто есть."""
    from admin_core.web import users

    unknown = await users.authenticate(pool, "nobody@test.ru", "любой-пароль")
    assert unknown["error"] == "invalid"
    assert unknown["message"] == "Неверный логин или пароль"


async def test_create_user_validates_input(demo, pool):
    from admin_core.web import users

    salon_id = str(demo["salon"]["id"])
    bad_role = await users.create_user(
        pool, login="x@test.ru", password="parol-12345", role="root", salon_id=salon_id
    )
    assert bad_role["ok"] is False and bad_role["error"] == "bad_role"

    bad_login = await users.create_user(
        pool, login="просто-логин", password="parol-12345", role="owner", salon_id=salon_id
    )
    assert bad_login["ok"] is False and bad_login["error"] == "bad_login"

    no_salon = await users.create_user(
        pool, login="x@test.ru", password="parol-12345", role="owner"
    )
    assert no_salon["ok"] is False and no_salon["error"] == "no_salon"

    first = await users.create_user(
        pool, login="dup@test.ru", password="parol-12345", role="owner", salon_id=salon_id
    )
    assert first["ok"] is True
    again = await users.create_user(
        pool, login="dup@test.ru", password="parol-12345", role="owner", salon_id=salon_id
    )
    assert again["ok"] is False and again["error"] == "exists"


async def test_panel_user_login_sets_role_in_session(demo, pool):
    """Сессия хранит роль: права проверяются на каждом действии."""
    from admin_core.web import users
    from admin_core.web.session import Panel

    salon_id = str(demo["salon"]["id"])
    await users.create_user(
        pool, login="staffer@test.ru", password="parol-12345", role="staff",
        salon_id=salon_id,
    )
    panel = Panel(pool=pool, password="")
    token, error = await panel.login_user("staffer@test.ru", "parol-12345")
    assert token and error == ""
    assert panel.role_of(token) == "staff"
    assert can_action(panel.role_of(token), "service_save") is False
    assert can_action(panel.role_of(token), "booking_status") is True

    bad, error = await panel.login_user("staffer@test.ru", "неверный")
    assert bad == "" and error == "invalid"


async def test_salon_for_session_isolates_tenant(demo, pool):
    """Владелец не может подменить чужой салон в ?salon= — вернётся его точка."""
    from admin_core.web.session import Panel, Session

    other = await pool.fetchrow(
        "INSERT INTO salons (name, timezone) VALUES ('Другой салон', 'Asia/Yekaterinburg') "
        "RETURNING id",
    )
    panel = Panel(pool=pool, password="")
    session = Session(
        user=None, role="owner", login="boss@test.ru",
        salon_id=str(demo["salon"]["id"]),
        until=datetime.now(ZoneInfo("UTC")) + timedelta(hours=1),
    )

    class _Req:
        query = {"salon": str(other["id"])}
        cookies: dict[str, str] = {}

    salon, salons = await panel.salon_for_session(_Req(), session)
    assert salon is not None
    assert str(salon["id"]) == str(demo["salon"]["id"])  # не чужой!
    assert len(salons) == 1


async def test_team_session_sees_all_salons(demo, pool):
    from admin_core.web.session import Panel, Session

    panel = Panel(pool=pool, password="")
    session = Session(
        user=None, role="team", login="admin@test.ru", salon_id=None,
        until=datetime.now(ZoneInfo("UTC")) + timedelta(hours=1),
    )

    class _Req:
        query: dict[str, str] = {}
        cookies: dict[str, str] = {}

    salon, salons = await panel.salon_for_session(_Req(), session)
    assert salon is not None and len(salons) > 1  # команда видит все точки


def test_login_page_has_login_password_and_totp_fields():
    base = login_page()
    assert 'name="login"' in base and 'name="password"' in base
    assert 'name="code"' not in base
    with_totp = login_page("Введите код", login="boss@test.ru", need_totp=True)
    assert 'name="code"' in with_totp
    assert 'value="boss@test.ru"' in with_totp  # логин не теряем при вводе кода
    assert "Введите код" in with_totp


def test_builder_signature_guard():
    """Регрессия: раздел со старой подписью ронял страницу в заглушку.

    Теперь рассинхрон ловится на старте сервиса — проверяем сам сторож.
    """

    def good(_panel, _salon, _salons, _request, _csrf=""):
        return ""

    def bad(_panel, _salon, _salons, _request):
        return ""

    check_builder_signatures({"/today": good})
    with pytest.raises(TypeError, match="audit"):
        check_builder_signatures({"/audit": bad})


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
