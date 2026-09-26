"""InternalCRM: слоты, создание записи, SlotTakenError (23P01), отмена, аудит."""

import pytest
from admin_core.crm.base import SlotTakenError


async def test_slots_and_booking(pool, demo):
    crm = demo["crm"]
    slots = await crm.get_free_slots(demo["salon"]["id"], "Тест стрижка")
    assert slots, "должны быть свободные слоты сегодня/завтра"
    first = slots[0]

    result = await crm.create_booking(
        demo["salon"]["id"], demo["client"]["id"], "Тест стрижка",
        first["start_text"], offered=slots, created_by="bot:test",
    )
    assert result["ok"] is True
    assert result["master_name"] == demo["master"]["name"]
    assert result["prepayment_required"] is False  # у тест-клиента флаг выключен

    # тот же слот ещё раз → SlotTakenError (исключение 23P01 из exclusion-констрейнта)
    with pytest.raises(SlotTakenError):
        await crm.create_booking(
            demo["salon"]["id"], demo["client"]["id"], "Тест стрижка",
            first["start_text"], offered=slots, created_by="bot:test",
        )

    # отмена освобождает слот
    cancelled = await crm.cancel_my_last_booking(demo["salon"]["id"], demo["client"]["id"])
    assert cancelled["ok"] is True
    slots_after = await crm.get_free_slots(demo["salon"]["id"], "Тест стрижка")
    assert any(s["start_text"] == first["start_text"] for s in slots_after)


async def test_client_identification_by_phone(pool, demo):
    crm = demo["crm"]
    again = await crm.get_client_by_phone(
        demo["salon"]["id"], demo["client"]["phone"]
    )
    assert again is not None
    assert str(again["id"]) == str(demo["client"]["id"])  # вопрос 7: узнаём «своих»


async def test_audit_log_written(pool, demo):
    crm = demo["crm"]
    slots = await crm.get_free_slots(demo["salon"]["id"], "Тест стрижка")
    await crm.create_booking(
        demo["salon"]["id"], demo["client"]["id"], "Тест стрижка",
        slots[0]["start_text"], offered=slots,
    )
    n = await pool.fetchval(
        "SELECT count(*) FROM audit_log WHERE salon_id = $1 AND action = 'create_booking'",
        demo["salon"]["id"],
    )
    assert n >= 1  # LIABILITY_OPTIONS.md: любой спор решается логом


async def test_prepayment_flag(pool, demo):
    """Вопрос 1: флаг на карточке клиента управляет prepayment_status записи."""
    await pool.execute(
        "UPDATE clients SET requires_prepayment = true WHERE id = $1",
        demo["client"]["id"],
    )
    slots = await demo["crm"].get_free_slots(demo["salon"]["id"], "Тест стрижка")
    result = await demo["crm"].create_booking(
        demo["salon"]["id"], demo["client"]["id"], "Тест стрижка",
        slots[0]["start_text"], offered=slots,
    )
    assert result["prepayment_required"] is True
    status = await pool.fetchval(
        "SELECT prepayment_status FROM bookings WHERE id = $1", result["booking_id"]
    )
    assert status == "offered"
