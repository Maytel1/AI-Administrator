"""Тесты веб-панели admin_core.web: рендер, форматирование, сессии, запросы.

Рендер проверяется без БД и без HTTP; запросы — на фикстуре ``demo``.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from admin_core.web import dashboard, queries
from admin_core.web.pages_core import page_bookings, page_today
from admin_core.web.pages_lists import (
    page_audit,
    page_clients,
    page_dialogs,
    page_masters,
    page_services,
    page_settings,
)
from admin_core.web.render import dt_local, esc, layout, login_page, money, table, tile
from admin_core.web.session import Panel

TZ = ZoneInfo("Asia/Yekaterinburg")
SALON = {"id": "11111111-1111-1111-1111-111111111111", "name": "Виктория",
         "plan": "trial", "crm_provider": "internal", "timezone": "Asia/Yekaterinburg"}
BOOKING = {
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
    html = layout("Сегодня", "/today", "<h1>Сегодня</h1>", salon=SALON, salons=[SALON])
    assert '<a href="/today?salon=' in html
    assert 'class="active"' in html
    assert "Виктория" in html  # переключатель салона
    assert "<h1>Сегодня</h1>" in html


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
        [{"name": "Иван", "phone": "+79001234567", "completed_visits": 2,
          "last_visit": datetime(2026, 9, 1, tzinfo=ZoneInfo("UTC")), "upcoming": 1,
          "requires_prepayment": True, "notes": ""}],
        TZ, SALON, [SALON],
    )
    assert "+79001234567" in html
    assert "требует предоплату" in html


def test_page_services_and_masters():
    svc = page_services(
        [{"title": "Маникюр", "duration_min": 120, "price": 2000, "is_active": True}],
        SALON, [SALON],
    )
    assert f"2{NBSP}000{NBSP}₽" in svc and "120 мин" in svc
    masters = page_masters(
        [{"name": "Мария", "is_active": True, "services_count": 2,
          "hours": '[{"weekday": 2, "start": "10:00:00", "end": "20:00:00"}]'}],
        SALON, [SALON],
    )
    assert "Ср 10:00–20:00" in masters
    assert "работает" in masters


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
    html = page_settings(SALON, [SALON], {"LLM_PROVIDER": "yandexgpt"})
    assert "Пробный" in html
    assert "yandexgpt" in html
    assert "только читает данные" in html


def test_tile_escapes_value():
    assert "<script>" not in tile("<script>", "плитка")
def test_panel_login_and_session_lifetime():
    panel = Panel(pool=None, password="secret")
    assert panel.login("wrong") is None
    token = panel.login("secret")
    assert token and panel.authorized(token)
    assert not panel.authorized("чужая-сессия")
    assert not panel.authorized("")
    panel.logout(token)
    assert not panel.authorized(token)


def test_panel_expired_session_is_dropped():
    panel = Panel(pool=None, password="secret")
    token = panel.login("secret")
    panel.sessions[token] = datetime.now(ZoneInfo("UTC")) - timedelta(seconds=1)
    assert not panel.authorized(token)
    assert token not in panel.sessions


def test_panel_without_password_never_logs_in():
    """Пустой пароль в env = вход закрыт, а не открыт для всех."""
    assert Panel(pool=None, password="").login("любой") is None


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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
