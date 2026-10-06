"""Тесты каталога из панели (услуги, мастера, клиенты) и отчётов."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from admin_core.web.actions import apply_form
from admin_core.web.roles import can_action

TZ = ZoneInfo("Asia/Yekaterinburg")
SALON = {
    "id": "11111111-1111-1111-1111-111111111111", "name": "Салон", "plan": "trial",
    "crm_provider": "internal", "timezone": "Asia/Yekaterinburg",
}


def _apply(pool, salon_id, form, actor="owner:boss@test.ru"):
    return apply_form(pool, salon_id, form, actor=actor)


async def test_owner_creates_service_from_panel(demo, pool):
    """Услуга, созданная в панели, сразу доступна боту в прайсе."""
    from admin_core.crm.internal import InternalCRM

    salon_id = str(demo["salon"]["id"])
    result = await _apply(
        pool, salon_id,
        {"action": "service_create", "title": "Диагностика ходовой",
         "price": "1800", "duration_min": "45"},
    )
    assert result["ok"] is True
    assert "Диагностика ходовой" in result["message"]
    titles = [s["title"] for s in await InternalCRM(pool).get_services(salon_id)]
    assert "Диагностика ходовой" in titles
    assert await pool.fetchval(
        "SELECT count(*) FROM audit_log WHERE action = 'create_service'"
    ) >= 1


async def test_service_create_validates_and_dedupes(demo, pool):
    salon_id = str(demo["salon"]["id"])
    empty = await _apply(
        pool, salon_id,
        {"action": "service_create", "title": "   ", "price": "100", "duration_min": "60"},
    )
    assert empty["ok"] is False
    bad_price = await _apply(
        pool, salon_id,
        {"action": "service_create", "title": "Ус", "price": "много", "duration_min": "60"},
    )
    assert bad_price["ok"] is False and bad_price["error"] == "bad_price"
    dupe = await _apply(
        pool, salon_id,
        {"action": "service_create", "title": "Тест стрижка", "price": "900",
         "duration_min": "60"},
    )
    assert dupe["ok"] is False and dupe["error"] == "exists"


async def test_service_archive_hides_from_bot(demo, pool):
    """Архив убирает услугу из прайса бота, но вернуть её можно одной кнопкой."""
    from admin_core.crm.internal import InternalCRM

    salon_id = str(demo["salon"]["id"])
    crm = InternalCRM(pool)
    service_id = str((await crm.get_services(salon_id))[0]["id"])
    off = await _apply(
        pool, salon_id,
        {"action": "service_archive", "service_id": service_id, "active": "0"},
    )
    assert off["ok"] is True
    assert all(str(s["id"]) != service_id for s in await crm.get_services(salon_id))
    on = await _apply(
        pool, salon_id,
        {"action": "service_archive", "service_id": service_id, "active": "1"},
    )
    assert on["ok"] is True
    assert any(str(s["id"]) == service_id for s in await crm.get_services(salon_id))


async def test_master_create_and_archive(demo, pool):
    from admin_core.crm.internal import InternalCRM

    salon_id = str(demo["salon"]["id"])
    crm = InternalCRM(pool)
    created = await _apply(
        pool, salon_id, {"action": "master_create", "name": "Ольга"}, actor="owner:b@test.ru"
    )
    assert created["ok"] is True
    masters = await crm.get_masters(salon_id)
    assert "Ольга" in [m["name"] for m in masters]
    olga = next(m for m in masters if m["name"] == "Ольга")
    dupe = await _apply(
        pool, salon_id, {"action": "master_create", "name": "Ольга"}, actor="owner:b@test.ru"
    )
    assert dupe["ok"] is False and dupe["error"] == "exists"
    off = await _apply(
        pool, salon_id,
        {"action": "master_archive", "master_id": olga["id"], "active": "0"},
    )
    assert off["ok"] is True
    assert all(m["name"] != "Ольга" for m in await crm.get_masters(salon_id))

async def test_manual_client_create(demo, pool):
    """Клиент, введённый вручную, получает ту же карточку, что и бот по телефону."""
    salon_id = str(demo["salon"]["id"])
    phone = "+79007778888"
    result = await _apply(
        pool, salon_id,
        {"action": "client_create", "phone": phone, "name": "Мария",
         "notes": "просит вечером", "requires_prepayment": "on"},
        actor="staff:recep@test.ru",
    )
    assert result["ok"] is True
    row = await pool.fetchrow(
        "SELECT name, notes, requires_prepayment FROM clients "
        "WHERE salon_id = $1 AND phone = $2",
        salon_id, phone,
    )
    assert row["name"] == "Мария"
    assert row["notes"] == "просит вечером"
    assert row["requires_prepayment"] is True
    # повторный ввод того же телефона не плодит карточек
    again = await _apply(
        pool, salon_id, {"action": "client_create", "phone": phone}, actor="staff:recep@test.ru"
    )
    assert again["ok"] is True
    assert await pool.fetchval(
        "SELECT count(*) FROM clients WHERE salon_id = $1 AND phone = $2", salon_id, phone
    ) == 1


async def test_manual_client_requires_phone(demo, pool):
    result = await _apply(
        pool, str(demo["salon"]["id"]),
        {"action": "client_create", "phone": "123"}, actor="staff:recep@test.ru",
    )
    assert result["ok"] is False and result["error"] == "bad_phone"


def test_role_permissions_for_catalog():
    """Справочник правит владелец и команда, администратор — только клиентов."""
    assert can_action("owner", "service_create") is True
    assert can_action("owner", "service_archive") is True
    assert can_action("staff", "service_create") is False
    assert can_action("staff", "master_create") is False
    assert can_action("staff", "client_create") is True   # это его рутина
    assert can_action("staff", "hours_save") is False
    assert can_action("staff", "hours_save_week") is False   # окно графика — не его роль
    assert can_action("staff", "master_services_save") is False  # как и услуги мастера
    assert can_action("owner", "master_services_save") is True


async def test_reports_count_revenue_and_funnel(demo, pool):
    """Отчёт считает выручку и воронку по фактическим записям."""
    from admin_core.crm.internal import InternalCRM
    from admin_core.web import reports

    crm = InternalCRM(pool)
    salon_id = str(demo["salon"]["id"])
    now = datetime.now(TZ)
    start = (now - timedelta(days=30)).astimezone(ZoneInfo("UTC"))
    end = (now + timedelta(days=1)).astimezone(ZoneInfo("UTC"))
    await crm.create_booking(
        salon_id, str(demo["client"]["id"]), "Тест стрижка", time_text="11:00", created_by="t"
    )
    by_day = await reports.revenue_by_day(pool, salon_id, start, end)
    assert by_day and float(by_day[0]["revenue"]) >= 1000
    load = await reports.load_by_master(pool, salon_id, start, end)
    assert any(m["name"] == "Тест Мастер" and m["bookings"] >= 1 for m in load)
    stats = await reports.funnel(pool, salon_id, start, end)
    assert stats["bookings"] >= 1
    assert stats["avg_check"] > 0
    assert 0.0 <= stats["conversion"] <= 100.0
    loss = await reports.cancellations(pool, salon_id, start, end)
    assert loss["total"] >= 1 and loss["lost_rate"] == 0.0


def test_reports_page_renders_chart_and_numbers():
    """График и плитки собираются из данных; при пустом периоде — честная пустота."""
    from admin_core.web.pages_lists import page_reports

    data = {
        "by_day": [{"day": datetime(2026, 10, 1), "revenue": 1000.0, "bookings": 2,
                    "from_bot": 1}],
        "load": [{"name": "Мария", "bookings": 2, "busy_hours": 1.5, "revenue": 1000.0}],
        "funnel": {"revenue": 1000.0, "bookings": 2, "dialogs": 5, "conversion": 40.0,
                   "avg_check": 500.0, "new_clients": 1, "llm_cost": 0.02, "completed": 1},
        "loss": {"total": 2, "cancelled": 0, "no_show": 0, "lost_rate": 0.0},
    }
    html = page_reports(data, SALON, [SALON])
    assert 'class="chart"' in html and "bar-fill" in html
    assert "40.0%" in html                    # конверсия (один знак после запятой)
    assert "Мария" in html                     # загрузка мастеров
    assert "Затраты на LLM" in html            # потери и затраты
    empty = page_reports(
        {"by_day": [], "load": [],
         "funnel": {**data["funnel"], "bookings": 0, "dialogs": 0, "revenue": 0.0,
                    "avg_check": 0.0, "conversion": 0.0},
         "loss": {"total": 0, "cancelled": 0, "no_show": 0, "lost_rate": 0.0}},
        SALON, [SALON],
    )
    assert "За период записей не было" in empty


if __name__ == "__main__":  # pragma: no cover
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))

async def test_master_with_future_bookings_cannot_be_archived(demo, pool):
    """Мастера с будущими записями не отключаем — записи останутся без него."""
    from admin_core.crm.internal import InternalCRM

    crm = InternalCRM(pool)
    salon_id = str(demo["salon"]["id"])
    await crm.create_booking(
        salon_id, str(demo["client"]["id"]), "Тест стрижка", time_text="11:00", created_by="t"
    )
    master_id = str(demo["master"]["id"])
    result = await _apply(
        pool, salon_id,
        {"action": "master_archive", "master_id": master_id, "active": "0"},
    )
    assert result["ok"] is False
    assert result["error"] == "has_bookings"
    assert "будущих записей" in result["message"]
    assert await pool.fetchval(
        "SELECT is_active FROM masters WHERE id = $1", master_id
    ) is True
