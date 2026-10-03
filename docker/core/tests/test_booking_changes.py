"""Перенос записи, приём предоплаты и метрики качества бота.

Перенос и предоплата закрывают две дыры: клиент не мог перенести визит, а
статусы предоплаты из миграции жили мёртвыми. Метрики бота — чтобы «бот
работает» было фактом, а не мнением.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from admin_core.bot_quality import bot_quality, looks_like_escalation
from admin_core.crm.internal import InternalCRM

MISSING = "00000000-0000-0000-0000-000000000000"


async def _make_booking(pool, demo: dict, *, status: str = "booked",
                       starts=None, created_by: str = "panel") -> str:
    """Создать запись напрямую: фикстура demo даёт салон, мастера и клиента."""
    start = starts or datetime.now(ZoneInfo("UTC")) + timedelta(days=1)
    booking_id = await pool.fetchval(
        "INSERT INTO bookings (salon_id, client_id, service_id, master_id, "
        "starts_at, ends_at, status, source, created_by) "
        "VALUES ($1, $2, $3, $4, $5::timestamptz, "
        "($5::timestamptz + interval '60 min'), $6, "
        "'bot', $7) RETURNING id",
        demo["salon"]["id"], demo["client"]["id"], demo["service"]["id"],
        demo["master"]["id"], start, status, created_by,
    )
    return str(booking_id)


async def _two_bookings(pool, demo: dict):
    """Две активные записи в разные дни — основа для проверки переноса."""
    base = datetime.now(ZoneInfo("UTC")).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    return [
        await _make_booking(pool, demo, starts=base + timedelta(days=1 + index))
        for index in range(2)
    ]


async def _dialog(pool, salon_id: str, chat_id: int, text: str) -> str:
    """Диалог с сообщением клиента: channel обязателен, chat_id — текст."""
    dialog_id = await pool.fetchval(
        "INSERT INTO dialogs (salon_id, chat_id, channel, client_id, started_at) "
        "VALUES ($1, $2, 'telegram', (SELECT id FROM clients WHERE salon_id = $1 "
        "ORDER BY id LIMIT 1), $3) RETURNING id",
        salon_id, str(chat_id),
        datetime.now(ZoneInfo("UTC")) - timedelta(days=1),
    )
    await pool.execute(
        "INSERT INTO dialog_messages (dialog_id, role, content) "
        "VALUES ($1, 'user', $2)",
        dialog_id, text,
    )
    return str(dialog_id)


# --------------------------------------------------------------------- перенос


async def test_reschedule_moves_booking_without_duplicating_it(demo, pool):
    """Перенос меняет время той же строки: дубля записи и фантома отмены нет."""
    salon_id = str(demo["salon"]["id"])
    crm = InternalCRM(pool)
    booking_id = (await _two_bookings(pool, demo))[0]
    before = await pool.fetchval(
        "SELECT starts_at FROM bookings WHERE id = $1", booking_id
    )
    count_before = await pool.fetchval(
        "SELECT count(*) FROM bookings WHERE salon_id = $1", salon_id
    )
    result = await crm.reschedule(
        salon_id, booking_id, before + timedelta(days=3), actor="test"
    )
    assert result["ok"] is True
    after = await pool.fetchrow(
        "SELECT starts_at, ends_at, status FROM bookings WHERE id = $1", booking_id
    )
    assert after["starts_at"] > before
    assert after["status"] == "booked", "перенос не должен менять статус"
    assert after["ends_at"] > after["starts_at"]
    assert await pool.fetchval(
        "SELECT count(*) FROM bookings WHERE salon_id = $1", salon_id
    ) == count_before, "перенос не создаёт новую запись"


async def test_reschedule_rejects_busy_slot(demo, pool):
    """Двойная бронь отсекается exclusion-ограничением БД, а не молча."""
    salon_id = str(demo["salon"]["id"])
    first, second = await _two_bookings(pool, demo)
    taken = await pool.fetchval("SELECT starts_at FROM bookings WHERE id = $1", second)
    result = await InternalCRM(pool).reschedule(salon_id, first, taken)
    assert result["ok"] is False and result["error"] == "slot_taken"
    assert "занято" in result["message"]


async def test_reschedule_rejects_unknown_and_finished_booking(demo, pool):
    """Чужая запись и запись, закрытая статусом, переносу не подлежат."""
    salon_id = str(demo["salon"]["id"])
    crm = InternalCRM(pool)
    missing = await crm.reschedule(
        salon_id, MISSING, datetime.now(ZoneInfo("UTC")) + timedelta(days=1)
    )
    assert missing == {"ok": False, "error": "not_found"}
    booking_id = (await _two_bookings(pool, demo))[0]
    await crm.set_booking_status(salon_id, booking_id, "cancelled", actor="test")
    finished = await crm.reschedule(
        salon_id, booking_id, datetime.now(ZoneInfo("UTC")) + timedelta(days=4)
    )
    assert finished["ok"] is False and finished["error"] == "bad_status"


async def test_reschedule_writes_audit(demo, pool):
    """Перенос попадает в аудит: время визита меняет администратор."""
    salon_id = str(demo["salon"]["id"])
    booking_id = (await _two_bookings(pool, demo))[0]
    before = await pool.fetchval(
        "SELECT starts_at FROM bookings WHERE id = $1", booking_id
    )
    await InternalCRM(pool).reschedule(
        salon_id, booking_id, before + timedelta(days=3), actor="test:audit"
    )
    assert await pool.fetchval(
        "SELECT count(*) FROM audit_log WHERE action = 'reschedule_booking' "
        "AND details::text LIKE '%' || $1 || '%'",
        booking_id,
    ) == 1
# ----------------------------------------------------------------- предоплата


async def test_prepayment_lifecycle(demo, pool):
    """Предоплата проходит путь: предложена → получена, с записью в аудит."""
    salon_id = str(demo["salon"]["id"])
    crm = InternalCRM(pool)
    booking_id = (await _two_bookings(pool, demo))[0]
    assert (await crm.set_prepayment(salon_id, booking_id, "offered"))["ok"]
    assert (await crm.set_prepayment(salon_id, booking_id, "paid"))["ok"]
    assert await pool.fetchval(
        "SELECT prepayment_status FROM bookings WHERE id = $1", booking_id
    ) == "paid"
    assert await pool.fetchval(
        "SELECT count(*) FROM audit_log WHERE action = 'set_prepayment'"
    ) >= 1


async def test_prepayment_rejects_unknown_status_and_foreign_booking(demo, pool):
    """Произвольный статус и несуществующая запись отклоняются."""
    salon_id = str(demo["salon"]["id"])
    crm = InternalCRM(pool)
    booking_id = (await _two_bookings(pool, demo))[0]
    assert (await crm.set_prepayment(salon_id, booking_id, "hacked"))["error"] == "bad_status"
    assert (await crm.set_prepayment(salon_id, MISSING, "paid"))["error"] == "not_found"


# --------------------------------------------------------------- метрики бота


async def test_bot_quality_counts_conversion_and_escalation(demo, pool):
    """Конверсия считается по диалогам, уходы к человеку — по словам клиента."""
    salon_id = str(demo["salon"]["id"])
    await _dialog(pool, salon_id, 5001, "хочу записаться на завтра")
    await _dialog(pool, salon_id, 5002, "соедините с человеком")
    await _make_booking(
        pool, demo, created_by="bot",
        starts=datetime.now(ZoneInfo("UTC")) - timedelta(days=1),
    )
    now = datetime.now(ZoneInfo("UTC"))
    stats = await bot_quality(pool, salon_id, now - timedelta(days=7), now)
    assert stats["dialogs"] >= 2
    assert stats["dialogs_with_booking"] >= 1
    assert 0 < stats["conversion"] <= 100
    assert stats["escalations"] >= 1
    assert stats["escalation_rate"] > 0
    assert stats["avg_turns"] >= 1
    assert stats["bookings_via_bot"] >= 1


async def test_bot_quality_on_empty_period_is_zero(demo, pool):
    """Пустой период даёт нули, а не исключение — страница должна открываться."""
    now = datetime.now(ZoneInfo("UTC"))
    stats = await bot_quality(
        pool, str(demo["salon"]["id"]),
        now - timedelta(days=500), now - timedelta(days=400),
    )
    assert stats["dialogs"] == 0 and stats["conversion"] == 0.0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Соедините с человеком", True),
        ("хочу живого оператора", True),
        ("позвоните мне потом", True),
        ("хочу записаться на завтра", False),
        ("сколько стоит стрижка", False),
    ],
)
def test_escalation_words(text: str, expected: bool) -> None:
    assert looks_like_escalation(text) is expected


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
