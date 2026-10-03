"""Тесты эксплуатационного контура: напоминания, лист ожидания, тарифы.

Это деньги в прямом смысле: каждое напоминание — минус неявка, каждая
предложенная запись — плюс к выручке. Поэтому проверяем не «функция вернула
True», а что произойдёт при повторе, гонке и неудачной отправке.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from admin_core import plans, reminders, waitlist
from admin_core.crm.internal import InternalCRM

TZ = ZoneInfo("Asia/Yekaterinburg")
UTC = ZoneInfo("UTC")


async def _booking(pool, salon, client):
    """Запись через обычный путь бота по реально свободному слоту.

    Раньше здесь было жёсткое «12:00» — и после полудня тест падал: слот
    уходил на следующий день, а get_free_slots отдаёт максимум 12 окон.
    Теперь берём первый свободный, и тест не зависит от времени суток.
    """
    crm = InternalCRM(pool)
    free = await crm.get_free_slots(str(salon["id"]), "Тест стрижка")
    assert free, "у тестового мастера должны быть свободные слоты"
    slot = free[0]
    return await crm.create_booking(
        str(salon["id"]), str(client["id"]), "Тест стрижка",
        time_text=slot["start_text"], offered=[slot], created_by="test",
    )


async def _freed_row(pool, booking_id):
    row = await pool.fetchrow(
        "SELECT b.id, b.starts_at, b.salon_id, sv.title AS service_title, "
        "m.name AS master_name FROM bookings b "
        "JOIN services sv ON sv.id = b.service_id JOIN masters m ON m.id = b.master_id "
        "WHERE b.id = $1",
        booking_id,
    )
    return dict(row)


# == 1. НАПОМИНАНИЯ =========================================================


def test_reminder_text_is_short_and_specific():
    """Напоминание содержит время, услугу и мастера — и ничего лишнего."""
    booking = {
        "starts_at": datetime(2026, 10, 5, 12, 0, tzinfo=UTC),
        "service_title": "Стрижка", "master_name": "Мария",
    }
    day = reminders.reminder_text(booking, "day", tz=TZ, salon_name="Виктория")
    assert "05.10 в 17:00" in day          # время в таймзоне салона, не UTC
    assert "Стрижка" in day and "Мария" in day and "Виктория" in day
    hours = reminders.reminder_text(booking, "hours", tz=TZ, salon_name="Виктория")
    assert "пару часов" in hours
    assert len(hours) < len(day)          # за 2 часа — короче


async def test_reminder_due_in_window_only(demo, pool):
    """Запись попадает в выборку только в своём окне, а не всегда."""
    created = await _booking(pool, demo["salon"], demo["client"])
    booking_id = created["booking_id"]
    starts = await pool.fetchval(
        "SELECT starts_at FROM bookings WHERE id = $1", booking_id
    )
    due = await reminders.due_reminders(
        pool, str(demo["salon"]["id"]), now=starts - timedelta(hours=24)
    )
    assert any(item["booking_id"] == str(booking_id) for item in due)
    early = await reminders.due_reminders(
        pool, str(demo["salon"]["id"]), now=starts - timedelta(days=3)
    )
    assert not any(item["booking_id"] == str(booking_id) for item in early)


async def test_reminder_is_not_sent_twice(demo, pool):
    """Повторный запуск worker'а не должен слать одно и то же напоминание."""
    created = await _booking(pool, demo["salon"], demo["client"])
    booking_id = str(created["booking_id"])
    starts = await pool.fetchval("SELECT starts_at FROM bookings WHERE id = $1", booking_id)
    now = starts - timedelta(hours=24)
    first = await reminders.due_reminders(pool, str(demo["salon"]["id"]), now=now)
    assert any(item["booking_id"] == booking_id for item in first)
    assert await reminders.claim_reminder(pool, booking_id, "day") is True
    second = await reminders.due_reminders(pool, str(demo["salon"]["id"]), now=now)
    assert not any(item["booking_id"] == booking_id for item in second)


async def test_failed_reminder_is_counted_and_not_repeated(demo, pool):
    """Неудачная отправка видна в статистике и не превращается в спам."""
    created = await _booking(pool, demo["salon"], demo["client"])
    booking_id = str(created["booking_id"])
    await reminders.mark_reminder_failed(pool, booking_id, "day")
    stats = await reminders.reminders_stats(
        pool, str(demo["salon"]["id"]), since=datetime.now(UTC) - timedelta(days=1)
    )
    assert stats["failed"] >= 1
    assert await reminders.claim_reminder(pool, booking_id, "day") is False


async def test_cancelled_booking_gets_no_reminder(demo, pool):
    """Отменённая запись не должна получать напоминание."""
    created = await _booking(pool, demo["salon"], demo["client"])
    booking_id = str(created["booking_id"])
    await InternalCRM(pool).set_booking_status(
        str(demo["salon"]["id"]), booking_id, "cancelled", actor="test"
    )
    starts = await pool.fetchval("SELECT starts_at FROM bookings WHERE id = $1", booking_id)
    due = await reminders.due_reminders(
        pool, str(demo["salon"]["id"]), now=starts - timedelta(hours=24)
    )
    assert not any(item["booking_id"] == booking_id for item in due)


# == 2. ЛИСТ ОЖИДАНИЯ =======================================================


async def test_waitlist_offer_goes_to_first_and_leaves_queue(demo, pool):
    """Первый в очереди получает предложение и снимается с неё."""
    created = await _booking(pool, demo["salon"], demo["client"])
    row = await _freed_row(pool, str(created["booking_id"]))
    crm = InternalCRM(pool)
    waiting = await crm.ensure_client(str(demo["salon"]["id"]), "+79001112233", "Ожидающий")
    await waitlist.add_to_waitlist(
        pool, str(demo["salon"]["id"]), str(waiting["id"]),
        service_id=str(demo["service"]["id"]),
    )
    offer = await waitlist.take_waitlist_offer(pool, row, tz=TZ, salon_name="Тест Салон")
    assert offer is not None
    assert offer["client_id"] == str(waiting["id"])
    again = await waitlist.take_waitlist_offer(pool, row, tz=TZ, salon_name="Тест Салон")
    assert again is None


async def test_waitlist_never_offers_to_canceller(demo, pool):
    """Клиент не получает предложение на собственную отменённую запись."""
    created = await _booking(pool, demo["salon"], demo["client"])
    row = await _freed_row(pool, str(created["booking_id"]))
    await waitlist.add_to_waitlist(
        pool, str(demo["salon"]["id"]), str(demo["client"]["id"])
    )
    offer = await waitlist.take_waitlist_offer(pool, row, tz=TZ, salon_name="Салон")
    assert offer is None


async def test_cancellation_offers_slot_to_waitlist(demo, pool):
    """Отмена записи сама запускает предложение листа ожидания."""
    crm = InternalCRM(pool)
    created = await _booking(pool, demo["salon"], demo["client"])
    waiting = await crm.ensure_client(str(demo["salon"]["id"]), "+79005556677", "Ждущий")
    await waitlist.add_to_waitlist(pool, str(demo["salon"]["id"]), str(waiting["id"]))
    result = await crm.set_booking_status(
        str(demo["salon"]["id"]), str(created["booking_id"]), "cancelled", actor="test"
    )
    offer = result.get("waitlist_offer")
    assert offer is not None
    assert offer["client_id"] == str(waiting["id"])


async def test_waitlist_is_tenant_scoped(demo, pool):
    """Лист ожидания одной точки не предлагается клиентам другой."""
    created = await _booking(pool, demo["salon"], demo["client"])
    row = await _freed_row(pool, str(created["booking_id"]))
    other = await pool.fetchrow(
        "INSERT INTO salons (name, timezone) VALUES ('чужой-то', 'Asia/Yekaterinburg') "
        "RETURNING id"
    )
    try:
        crm = InternalCRM(pool)
        foreign = await crm.ensure_client(str(other["id"]), "+79008889999", "Чужой")
        await waitlist.add_to_waitlist(pool, str(other["id"]), str(foreign["id"]))
        offer = await waitlist.take_waitlist_offer(pool, row, tz=TZ, salon_name="Салон")
        assert offer is None
    finally:
        await pool.execute("DELETE FROM salons WHERE id = $1", other["id"])


async def test_waitlist_respects_service_filter(demo, pool):
    """Клиент, ждущий другую услугу, не получает предложение по чужой."""
    crm = InternalCRM(pool)
    created = await _booking(pool, demo["salon"], demo["client"])
    row = await _freed_row(pool, str(created["booking_id"]))
    other_service = await pool.fetchrow(
        "INSERT INTO services (salon_id, title, duration_min, price) "
        "VALUES ($1, 'Маникюр', 30, 500) RETURNING id",
        demo["salon"]["id"],
    )
    waiting = await crm.ensure_client(str(demo["salon"]["id"]), "+79004443322", "Маникюр")
    await waitlist.add_to_waitlist(
        pool, str(demo["salon"]["id"]), str(waiting["id"]),
        service_id=str(other_service["id"]),
    )
    offer = await waitlist.take_waitlist_offer(pool, row, tz=TZ, salon_name="Салон")
    assert offer is None


# == 3. ТАРИФЫ И ЛИМИТЫ =====================================================


def test_plan_limits_table_is_complete():
    """У всех тарифов одинаковый набор ресурсов — иначе сравнение бессмысленно."""
    keys = {tuple(sorted(v)) for v in plans.PLAN_LIMITS.values()}
    assert len(keys) == 1, "наборы ресурсов в тарифах расходятся"
    assert plans.limit_of("pro", "clients") is None      # без ограничения
    assert plans.limit_of("start", "clients") == 300


async def test_trial_limit_blocks_new_service(demo, pool):
    """На триале услуг сверх лимита не добавить, и причина понятна человеку."""
    await pool.execute(
        "UPDATE salons SET plan = 'trial' WHERE id = $1", demo["salon"]["id"]
    )
    crm = InternalCRM(pool)
    existing = await pool.fetchval(
        "SELECT count(*) FROM services WHERE salon_id = $1", demo["salon"]["id"]
    )
    for i in range(plans.limit_of("trial", "services") - int(existing)):
        result = await crm.create_service(
            str(demo["salon"]["id"]), title=f"Услуга {i}", duration_min=30, price=100
        )
        assert result["ok"] is True
    blocked = await crm.create_service(
        str(demo["salon"]["id"]), title="Лишняя", duration_min=30, price=100
    )
    assert blocked["ok"] is False
    assert blocked["error"] == "plan_limit"
    assert "триал" in blocked["message"] or "тарифе" in blocked["message"]


async def test_existing_client_does_not_hit_limit(demo, pool):
    """Повторный ввод того же телефона не упирается в лимит карточек."""
    await pool.execute(
        "UPDATE salons SET plan = 'trial' WHERE id = $1", demo["salon"]["id"]
    )
    crm = InternalCRM(pool)
    for _ in range(3):
        result = await crm.create_client_manual(
            str(demo["salon"]["id"]), phone="+79001119999"
        )
        assert result["ok"] is True


async def test_usage_report_shows_headroom(demo, pool):
    """Отчёт по тарифу показывает, что занято и сколько ещё можно."""
    report = await plans.usage_report(
        pool, {**demo["salon"], "plan": "standard", "timezone": "Asia/Yekaterinburg"}
    )
    resources = {row["resource"]: row for row in report["rows"]}
    assert resources["services"]["used"] >= 1
    assert resources["services"]["limit"] == 150
    assert report["at_limit"] == []


if __name__ == "__main__":  # pragma: no cover
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
