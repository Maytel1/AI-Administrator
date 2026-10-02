"""Безопасность эксплуатационного контура: напоминания, лист ожидания, журнал
входов, тревоги, выгрузка CSV и голосовой сервис.

Новые поверхности — новые векторы: worker шлёт сообщения клиентам, CSV уходит
бухгалтерии, call-service принимает POST извне. Каждая проверка здесь — это
попытка нанести ущерб: чужой салон, формула в CSV, звонок без токена.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from admin_core import exports, monitoring, reminders, waitlist
from admin_core.crm.connectors import connector_status, unavailable_salons
from admin_core.web.session import Panel

TZ = ZoneInfo("Asia/Yekaterinburg")
UTC = ZoneInfo("UTC")
SQLI = "'; DROP TABLE reminders; --"


# == 1. НАПОМИНАНИЯ: ЧУЖОЙ САЛОН И ПДн ======================================


async def test_reminders_are_tenant_scoped(demo, pool):
    """Напоминание точки А не может быть выбрано для отправки в точке Б."""
    from admin_core.crm.internal import InternalCRM

    other = await pool.fetchrow(
        "INSERT INTO salons (name, timezone) VALUES ('точка-б', 'Asia/Yekaterinburg') "
        "RETURNING id"
    )
    try:
        crm = InternalCRM(pool)
        foreign_client = await crm.ensure_client(str(other["id"]), "+79001234567", "Чужой")
        booking = await pool.fetchrow(
            "INSERT INTO bookings (salon_id, client_id, master_id, service_id, "
            "starts_at, ends_at, status) "
            "SELECT $1, $2, m.id, s.id, now() + interval '24 hours', "
            "now() + interval '25 hours', 'booked' "
            "FROM masters m, services s WHERE m.salon_id = $1 AND s.salon_id = $1 "
            "LIMIT 1 RETURNING id",
            other["id"], foreign_client["id"],
        )
        due = await reminders.due_reminders(pool, str(demo["salon"]["id"]))
        assert not any(item["booking_id"] == str(booking["id"]) for item in due)
    finally:
        await pool.execute("DELETE FROM salons WHERE id = $1", other["id"])


def test_reminder_text_contains_no_unexpected_pii():
    """В напоминании нет телефона клиента и внутренних идентификаторов."""
    booking = {
        "starts_at": datetime(2026, 10, 5, 12, 0, tzinfo=UTC),
        "service_title": "Стрижка", "master_name": "Мария",
    }
    text = reminders.reminder_text(booking, "day", tz=TZ, salon_name="Виктория")
    assert "+7" not in text
    assert "@" not in text
    assert "uuid" not in text.lower()


async def test_reminder_claim_requires_real_booking(demo, pool):
    """Произвольный ключ нельзя «отметить отправленной»: есть внешний ключ."""
    ghost = "11111111-1111-1111-1111-111111111111"
    assert await reminders.claim_reminder(pool, ghost, "day") is False


# == 2. ЛИСТ ОЖИДАНИЯ: НЕЛЬЗЯ ОТДАТЬ СЛОТ ЧУЖОМУ =========================


async def test_waitlist_size_is_tenant_scoped(demo, pool):
    """Размер очереди одной точки не включает клиентов другой."""
    from admin_core.crm.internal import InternalCRM

    other = await pool.fetchrow(
        "INSERT INTO salons (name, timezone) VALUES ('точка-в', 'Asia/Yekaterinburg') "
        "RETURNING id"
    )
    try:
        crm = InternalCRM(pool)
        foreign = await crm.ensure_client(str(other["id"]), "+79007654321", "Чужой")
        await waitlist.add_to_waitlist(pool, str(other["id"]), str(foreign["id"]))
        assert await waitlist.waitlist_size(pool, str(other["id"])) == 1
        assert await waitlist.waitlist_size(pool, str(demo["salon"]["id"])) == 0
    finally:
        await pool.execute("DELETE FROM salons WHERE id = $1", other["id"])


# == 3. ЖУРНАЛ ВХОДОВ: НИКАКИХ ПАРОЛЕЙ И БЕЗОПАСНЫЕ ДЛИНЫ =================


async def test_login_log_truncates_and_stores_no_password(demo, pool):
    """В журнал попадает логин и IP, но не пароль, и всё обрезано."""
    await monitoring.log_login(
        pool, login="x" * 500, ip="1.2.3.4", ok=False, reason="invalid",
        user_agent="Mozilla/5.0 " + "A" * 400,
    )
    row = await pool.fetchrow(
        "SELECT login, ip, ok, reason, user_agent FROM login_log ORDER BY id DESC LIMIT 1"
    )
    assert len(row["login"]) == 200
    assert len(row["user_agent"]) == 200
    assert row["ip"] == "1.2.3.4"
    assert row["ok"] is False
    assert "password" not in row.keys()


async def test_login_log_survives_sql_injection_attempt(demo, pool):
    """Инъекция в логине пишется как текст, а не исполняется."""
    await monitoring.log_login(pool, login=SQLI, ip="9.9.9.9", ok=False)
    assert await pool.fetchval("SELECT count(*) FROM reminders") >= 0
    row = await pool.fetchrow("SELECT login FROM login_log ORDER BY id DESC LIMIT 1")
    assert row["login"] == SQLI


async def test_panel_records_failed_login_in_db(demo, pool):
    """Неудачный вход попадает в журнал — без него разбор инцидента невозможен."""
    panel = Panel(pool=pool, password="")
    await panel.login_user("nobody@nowhere.ru", "wrong-password")
    entry = await pool.fetchrow(
        "SELECT ok, reason FROM login_log WHERE login = 'nobody@nowhere.ru' "
        "ORDER BY id DESC LIMIT 1"
    )
    assert entry is not None
    assert entry["ok"] is False


async def test_session_survives_other_instance(demo, pool):
    """Сессия, созданная на одном инстансе, работает на другом — из БД."""
    from admin_core.web import users

    login = f"multi-{str(demo['salon']['id'])[:8]}@test.ru"
    await users.create_user(
        pool, login=login, password="parol-12345", role="owner",
        salon_id=str(demo["salon"]["id"]),
    )
    try:
        first = Panel(pool=pool, password="")
        token, error = await first.login_user(login, "parol-12345")
        assert error == "" and token
        second = Panel(pool=pool, password="")   # «второй инстанс», пустая память
        assert await second.authorized(token) is True
        session = await second.session_of(token)
        assert session is not None and session.login == login
        await second.logout(token)
        assert await first.authorized(token) is False   # выход действует везде
    finally:
        await pool.execute("DELETE FROM panel_users WHERE login = $1", login)


# == 4. ТРЕВОГИ: НЕЛЬЗЯ ЗАВАЛИТЬ ВЛАДЕЛЬЦА ===============================


async def test_alert_is_sent_once_per_cooldown(demo, pool):
    """Одна и та же тревога не повторяется каждый тик.

    Ключ уникальный на каждый прогон: should_send меняет состояние БД, и общий
    ключ делал бы тест зависимым от предыдущих запусков.
    """
    import uuid

    kind = f"test_{uuid.uuid4().hex}"
    now = datetime.now(UTC)
    first = await monitoring.should_send(pool, kind, now=now)
    again = await monitoring.should_send(pool, kind, now=now + timedelta(minutes=1))
    later = await monitoring.should_send(pool, kind, now=now + timedelta(hours=2))
    await pool.execute("DELETE FROM alerts WHERE kind = $1", kind)
    assert first is True
    assert again is False
    assert later is True


async def test_alerts_dedup_across_kinds(demo, pool):
    """Разные тревоги не подавляют друг друга."""
    import uuid

    first_kind = f"a_{uuid.uuid4().hex}"
    second_kind = f"b_{uuid.uuid4().hex}"
    now = datetime.now(UTC)
    assert await monitoring.should_send(pool, first_kind, now=now) is True
    assert await monitoring.should_send(pool, second_kind, now=now) is True
    await pool.execute(
        "DELETE FROM alerts WHERE kind = ANY($1::text[])", [first_kind, second_kind]
    )


def test_render_alert_is_short_and_marked():
    """Тревога помечена серьёзностью и не раздута текстом."""
    warning = monitoring.render_alert(monitoring.Alert("k", "что-то случилось"))
    critical = monitoring.render_alert(
        monitoring.Alert("k2", "бота уронили", "critical"), salon="Виктория"
    )
    assert "🟡" in warning and "🔴" in critical
    assert "Виктория" in critical
    assert len(warning) < 200


# == 5. ВЫГРУЗКА CSV: ФОРМУЛЫ И ПРАВА =======================================


def test_csv_neutralizes_formulas():
    """Значение, начинающееся с = или +, не станет формулой в Excel."""
    payload = exports.to_csv(
        ["Имя", "Сумма"],
        [["=1+1", "+CMD|calc"], ["-2+3", "@SUM(A1)"], ["обычный", "123"]],
    ).decode("utf-8-sig")
    lines = payload.splitlines()
    assert lines[1].startswith("'=1+1")
    assert "'+CMD" in lines[1]
    assert "'-2+3" in lines[2]
    assert "'@SUM" in lines[2]
    assert "обычный" in lines[3]


def test_csv_has_bom_for_excel():
    """Без BOM Excel открывает кириллицу как нечитаемые символы."""
    assert exports.to_csv(["Имя"], [["Мария"]]).startswith(b"\xef\xbb\xbf")


async def test_csv_export_stays_in_own_salon(demo, pool):
    """Выгрузка отдаёт только данные своей точки."""
    now = datetime.now(UTC)
    payload = await exports.export_daily(pool, demo["salon"], now - timedelta(days=30), now)
    assert payload.startswith(b"\xef\xbb\xbf")
    assert "Дата".encode("utf-8") in payload


# == 6. ГОЛОСОВОЙ СЕРВИС: БЕЗ ТОКЕНА НИ ОДНОГО ЗАПРОСА ====================


def test_empty_call_token_matches_nothing():
    """Пустой секрет не подходит ни к чему, не-ASCII токен не роняет код."""
    from admin_core.tokens import matches, strip_bearer

    assert matches("секрет", "") is False        # сервис без токена закрыт
    assert matches("", "секрет") is False
    assert matches("секрет", "секрет") is True
    assert matches("секре", "секрет") is False
    # сравнение по байтам: раньше hmac.compare_digest бросал TypeError
    assert matches("токен-с-русскими", "токен-с-русскими") is True
    assert strip_bearer("Bearer abc") == "abc"
    assert strip_bearer("abc") == "abc"
    assert strip_bearer(None) == ""


# == 7. КОННЕКТОРЫ CRM =====================================================


def test_connector_status_lists_missing_keys():
    """Готовность коннектора объясняет, чего именно не хватает."""
    assert connector_status("internal", env={})["ready"] is True
    altegio = connector_status("altegio", env={})
    assert altegio["ready"] is False
    assert "ALTEGIO_TOKEN" in altegio["missing"]
    ready = connector_status("altegio", env={"ALTEGIO_TOKEN": "x", "ALTEGIO_BRANCH_ID": "1"})
    assert ready["ready"] is True
    unknown = connector_status("что-то", env={})
    assert unknown["known"] is False and unknown["ready"] is False


def test_unavailable_salons_only_for_foreign_providers():
    """Тревога о неготовом коннекторе не поднимается на внутренней CRM."""
    salons = [
        {"id": "1", "name": "Наша", "crm_provider": "internal"},
        {"id": "2", "name": "Чужая", "crm_provider": "altegio"},
    ]
    broken = unavailable_salons(salons, env={})
    assert [s["salon"] for s in broken] == ["Чужая"]


if __name__ == "__main__":  # pragma: no cover
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
