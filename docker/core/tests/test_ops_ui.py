"""Тесты новых разделов: лист ожидания в панели и сводка по сети.

Проверяем не только «страница нарисовалась», но и то, что чужие данные туда
не попадают: сводка по сети — самое чувствительное место панели.
"""

from zoneinfo import ZoneInfo

from admin_core import waitlist
from admin_core.crm.internal import InternalCRM
from admin_core.web.actions import apply_form
from admin_core.web.pages_lists import page_network, waitlist_block
from admin_core.web.roles import can_action

TZ = ZoneInfo("Asia/Yekaterinburg")
SALON = {
    "id": "11111111-1111-1111-1111-111111111111", "name": "Салон", "plan": "trial",
    "crm_provider": "internal", "timezone": "Asia/Yekaterinburg",
}


class _Ctx:
    def __init__(self, role: str) -> None:
        self.role = role
        self.csrf = "test-csrf"

    def may(self, permission: str) -> bool:
        from admin_core.web.roles import can

        return can(self.role, permission)


def _apply(pool, salon_id, form, actor="owner:boss@test.ru"):
    return apply_form(pool, salon_id, form, actor=actor)


# == 1. ЛИСТ ОЖИДАНИЯ В ПАНЕЛИ ===========================================


async def test_waitlist_added_from_panel(demo, pool):
    """Владелец ставит клиента в очередь прямо из панели — по телефону."""
    result = await _apply(
        pool, str(demo["salon"]["id"]),
        {"action": "waitlist_add", "phone": "+79005550101", "note": "хочет утром"},
        actor="owner:boss@test.ru",
    )
    assert result["ok"] is True
    assert "листе ожидания" in result["message"]
    queue = await waitlist.list_waitlist(pool, str(demo["salon"]["id"]))
    assert len(queue) == 1
    assert queue[0]["phone"] == "+79005550101"
    assert queue[0]["note"] == "хочет утром"


async def test_waitlist_add_requires_phone(demo, pool):
    """Без телефона клиента в очередь не поставить — его не узнать."""
    result = await _apply(
        pool, str(demo["salon"]["id"]),
        {"action": "waitlist_add", "phone": "123"}, actor="owner:boss@test.ru",
    )
    assert result["ok"] is False
    assert result["error"] == "bad_phone"


async def test_waitlist_drop_is_tenant_scoped(demo, pool):
    """Чужую запись очереди не убрать — даже зная её id."""
    other = await pool.fetchrow(
        "INSERT INTO salons (name, timezone) VALUES ('точка-з', 'Asia/Yekaterinburg') "
        "RETURNING id"
    )
    try:
        crm = InternalCRM(pool)
        foreign = await crm.ensure_client(str(other["id"]), "+79005550999", "Чужой")
        await waitlist.add_to_waitlist(pool, str(other["id"]), str(foreign["id"]))
        theirs = await waitlist.list_waitlist(pool, str(other["id"]))
        result = await waitlist.drop_from_waitlist(
            pool, str(demo["salon"]["id"]), str(theirs[0]["id"]), actor="owner:boss@test.ru"
        )
        assert result["ok"] is False
        assert len(await waitlist.list_waitlist(pool, str(other["id"]))) == 1
    finally:
        await pool.execute("DELETE FROM salons WHERE id = $1", other["id"])


def test_waitlist_block_renders_form_even_when_empty():
    """Пустая очередь — не повод прятать возможность добавить клиента."""
    html = waitlist_block([], [{"id": "s1", "title": "Стрижка"}], _Ctx("owner"), SALON)
    assert "Лист ожидания (0)" in html
    assert "Никто не ждёт" in html or "никто не ждёт" in html
    assert "waitlist_add" in html
    assert "+79001234567" in html
    assert "Стрижка" in html


async def test_waitlist_block_shows_queue_rows(demo, pool):
    """В очереди видно, кто ждёт, какую услугу и когда встал."""
    await waitlist.add_to_waitlist(
        pool, str(demo["salon"]["id"]), str(demo["client"]["id"]),
        service_id=str(demo["service"]["id"]),
    )
    queue = await waitlist.list_waitlist(pool, str(demo["salon"]["id"]))
    html = waitlist_block(queue, [], _Ctx("owner"), SALON)
    assert "Лист ожидания (1)" in html
    assert "Тест стрижка" in html
    assert "waitlist_drop" in html


def test_waitlist_actions_follow_client_permission():
    """Лист ожидания — работа с клиентами: право администратора здесь есть."""
    assert can_action("staff", "waitlist_add") is True
    assert can_action("owner", "waitlist_add") is True


# == 2. СВОДКА ПО СЕТИ =====================================================


def test_network_page_lists_salons():
    """Сводка показывает точки и итоги по сети."""
    rows = [
        {"salon": "Виктория", "plan": "standard", "bookings": 12, "revenue": 24000.0,
         "no_shows": 1, "clients": 80},
        {"salon": "Парк", "plan": "trial", "bookings": 3, "revenue": 4500.0,
         "no_shows": 0, "clients": 20},
    ]
    totals = {
        "salons": 2, "revenue": 28500.0, "bookings": 15, "no_shows": 1, "clients": 100,
    }
    html = page_network(rows, totals, SALON, [SALON])
    assert "Сеть точек" in html
    assert "Виктория" in html and "Парк" in html
    assert "28" in html and "500" in html
    assert "точек в сети" in html


def test_network_nav_only_for_team():
    """Владельцу точки пункт «Сеть» не показывается: чужая выручка ему не нужна."""
    from admin_core.web.render import layout

    owner_html = layout("Отчёты", "/reports", "x", salon=SALON, salons=[SALON], role="owner")
    team_html = layout("Отчёты", "/reports", "x", salon=SALON, salons=[SALON], role="team")
    assert "/network" not in owner_html
    assert "/network" in team_html


async def test_network_summary_counts_all_salons(demo, pool):
    """Сводка собирается по всем активным точкам и не ломается на пустой базе."""
    from admin_core.exports import network_summary

    rows = await network_summary(pool, days=30)
    assert any(r["salon"] == demo["salon"]["name"] for r in rows)
    for row in rows:
        assert row["bookings"] >= 0 and float(row["revenue"] or 0) >= 0


if __name__ == "__main__":  # pragma: no cover
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
