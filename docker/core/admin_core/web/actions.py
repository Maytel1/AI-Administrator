"""Правки из панели: разбор формы → вызов CRM → понятный результат.

Здесь только HTTP-забота (валидация ввода, коды ошибок, тексты для человека);
бизнес-правила и аудит — в ``admin_core.crm.internal.InternalCRM``.
Все проверки обязаны передавать ``salon_id`` — чужая запись не правится.
"""

from __future__ import annotations

from datetime import time
from typing import Any

from admin_core.crm.internal import InternalCRM

MIN_PRICE = 0
MAX_PRICE = 10_000_000
MIN_DURATION = 5
MAX_DURATION = 600

ERRORS = {
    "not_found": "Запись не найдена в этой точке (возможно, она уже удалена)",
    "bad_status": "Такого статуса не существует",
    "bad_weekday": "Нет такого дня недели",
    "bad_price": f"Цена должна быть от {MIN_PRICE} до {MAX_PRICE:,.0f} ₽".replace(",", " "),
    "bad_duration": f"Длительность — от {MIN_DURATION} до {MAX_DURATION} минут",
    "bad_time": "Время в формате ЧЧ:ММ, например 10:00",
    "nothing_to_update": "Нечего сохранять",
    "save_failed": "Не сохранилось — попробуйте ещё раз",
}

WEEKDAY_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def _int(form: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(str(form.get(key, default)).strip() or default)
    except ValueError:
        return default


def _hhmm(form: dict[str, Any], key: str) -> time | None:
    """'10:0' → '10:00'; мусор и пустое значение отбрасываем."""
    raw = str(form.get(key, "")).strip()
    if not raw:
        return None
    try:
        hour, _, minute = raw.partition(":")
        hh, mm = int(hour), int(minute)
    except ValueError:
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return time(hh, mm)


def _hhmm_text(value: time | None) -> str:
    """time → 'ЧЧ:ММ' — для текста сообщения администратору."""
    return value.strftime("%H:%M") if value else "—"


def ok(message: str) -> dict[str, Any]:
    return {"ok": True, "message": message}


def fail(code: str, message: str = "") -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message or ERRORS.get(code, code)}


async def apply_form(pool: Any, salon_id: str, form: dict[str, Any]) -> dict[str, Any]:
    """Единая точка входа для всех форм панели.

    ``form["action"]`` — что делаем; остальные поля зависят от действия.
    Наружу отдаём словарь с ``ok`` и понятным человеку ``message``.
    """
    crm = InternalCRM(pool)
    action = str(form.get("action", ""))

    if action == "booking_status":
        booking_id = str(form.get("booking_id", ""))
        status = str(form.get("status", ""))
        if not booking_id:
            return fail("not_found")
        result = await crm.set_booking_status(salon_id, booking_id, status)
        if not result["ok"]:
            return fail(result.get("error", "save_failed"))
        label = {
            "confirmed": "Запись подтверждена",
            "completed": "Запись отмечена как выполненная",
            "cancelled": "Запись отменена — слот освободился",
            "no_show": "Отмечено: клиент не пришёл",
            "booked": "Запись снова активна",
        }.get(status, "Статус записи обновлён")
        return ok(label)

    if action == "service_save":
        service_id = str(form.get("service_id", ""))
        price = _decimal(form.get("price"))
        duration = _int(form, "duration_min", 0)
        active = "is_active" in form
        if price is None or not MIN_PRICE <= price <= MAX_PRICE:
            return fail("bad_price")
        if not MIN_DURATION <= duration <= MAX_DURATION:
            return fail("bad_duration")
        result = await crm.update_service(
            salon_id, service_id, price=price, duration_min=duration, is_active=active
        )
        if not result["ok"]:
            return fail(result.get("error", "save_failed"))
        state = "активна" if active else "выключена"
        return ok(f"«{result['title']}» сохранена: {_money(price)}, {duration} мин, {state}")

    if action == "client_save":
        client_id = str(form.get("client_id", ""))
        notes = str(form.get("notes", "")).strip()
        prepay = "requires_prepayment" in form
        result = await crm.set_client_flags(
            salon_id, client_id, requires_prepayment=prepay, notes=notes
        )
        if not result["ok"]:
            return fail(result.get("error", "save_failed"))
        prepay_word = "предоплата включена" if prepay else "предоплата выключена"
        return ok(f"Клиент {result['phone']}: {prepay_word}, заметки сохранены")

    if action == "hours_save":
        master_id = str(form.get("master_id", ""))
        weekday = _int(form, "weekday", -1)
        enabled = "enabled" in form
        start, end = _hhmm(form, "start"), _hhmm(form, "end")
        if not 0 <= weekday <= 6:
            return fail("bad_weekday")
        if enabled and (start is None or end is None):
            return fail("bad_time")
        if enabled and start and end and start >= end:
            return fail("bad_time", "Начало должно быть раньше конца")
        outside = 0
        if enabled and start and end:
            outside = await crm.count_bookings_outside_hours(
                salon_id, master_id, weekday, start, end
            )
        result = await crm.set_working_hours(
            salon_id, master_id, weekday, start=start, end=end, enabled=enabled
        )
        if not result["ok"]:
            return fail(result.get("error", "save_failed"))
        day = WEEKDAY_RU[weekday]
        if not enabled:
            return ok(f"{result['master']}: {day} — нерабочий")
        text = f"{result['master']}: {day} {_hhmm_text(start)}–{_hhmm_text(end)}"
        if outside:
            text += f". Внимание: {outside} записей выпадут из окна"
        return ok(text)

    return fail("save_failed", "Неизвестное действие формы")


def _decimal(raw: Any) -> float | None:
    """'2 000,50' / '2000.5' → float; мусор → None, чтобы показать свою ошибку."""
    text = str(raw or "").replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _money(value: float) -> str:
    return f"{int(round(value)):,}".replace(",", " ") + " ₽"
