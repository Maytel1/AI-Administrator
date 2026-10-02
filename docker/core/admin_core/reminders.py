"""Напоминания клиентам и лист ожидания.

Неявка — это потерянный чек, а напоминание стоит копейки. Здесь вся логика
выбора записей и текстов сообщений; сама отправка — в worker'е.

Два важных свойства:
* **без дублей** — уникальный ключ (booking_id, kind) в БД, повторный запуск
  worker'а не отправит «напомни за сутки» дважды;
* **без ПДн в лишних местах** — в тексте только то, что клиент и так знает:
  услуга, время, мастер, адрес точки.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

# Когда напоминать: (ключ, за сколько часов до визита).
REMINDER_STEPS: tuple[tuple[str, int], ...] = (("day", 24), ("hours", 2))


@dataclass(frozen=True)
class ReminderText:
    """Готовый текст напоминания и подпись кнопки подтверждения."""

    text: str
    booking_id: str
    kind: str
    can_confirm: bool = True


def _fmt(dt: datetime, tz: ZoneInfo) -> str:
    local = dt.astimezone(tz)
    return local.strftime("%d.%m в %H:%M")


def reminder_text(
    booking: dict[str, Any], kind: str, *, tz: ZoneInfo, salon_name: str,
) -> str:
    """Текст напоминания.

    Без «вы записаны» в напоминании за 2 часа: клиент это уже знает, лишние слова
    только мешают прочитать главное — время.
    """
    service = booking.get("service_title") or "услуга"
    master = booking.get("master_name") or "мастер"
    when = _fmt(booking["starts_at"], tz)
    if kind == "day":
        return (
            f"Здравствуйте! Напоминаем о записи в салон «{salon_name}»:\n"
            f"• {service}\n• {when}\n• мастер {master}\n\n"
            "Подтвердите визит или перенесите его — кнопки ниже."
        )
    if kind == "hours":
        return (
            f"Через пару часов у вас визит: {service}, {when}, мастер {master}.\n"
            f"Салон «{salon_name}». Если планы изменились — предупредите, пожалуйста."
        )
    return f"Запись {when}: {service}, мастер {master}, салон «{salon_name}»."


async def due_reminders(
    pool: Any, salon_id: str, *, now: datetime | None = None,
    steps: tuple[tuple[str, int], ...] = REMINDER_STEPS,
) -> list[dict[str, Any]]:
    """Записи, которым пора напомнить, и текст к каждой.

    Окно в 10 минут шире шага: иначе минутный тик worker'а может пролететь
    между девятью и десятью минутами и напоминание потеряется навсегда.
    """
    moment = now or datetime.now(ZoneInfo("UTC"))
    rows = await pool.fetch(
        """
        SELECT b.id, b.starts_at, b.status,
               s.title AS service_title, m.name AS master_name,
               sa.name AS salon_name, sa.timezone
        FROM bookings b
        JOIN services sv ON sv.id = b.service_id
        LEFT JOIN services s ON s.id = sv.id
        LEFT JOIN masters m ON m.id = b.master_id
        JOIN salons sa ON sa.id = b.salon_id
        WHERE b.salon_id = $1 AND b.status IN ('booked','confirmed')
        """,
        salon_id,
    )
    ready: list[dict[str, Any]] = []
    for row in rows:
        starts_at = row["starts_at"]
        for kind, hours_before in steps:
            send_at = starts_at - timedelta(hours=hours_before)
            if not (send_at <= moment < send_at + timedelta(minutes=10)):
                continue
            already = await pool.fetchval(
                "SELECT 1 FROM reminders WHERE booking_id = $1 AND kind = $2",
                row["id"], kind,
            )
            if already:
                continue
            tz = ZoneInfo(row["timezone"] or "Asia/Yekaterinburg")
            ready.append({
                "booking_id": str(row["id"]),
                "starts_at": starts_at,
                "client_phone": None,
                "kind": kind,
                "text": reminder_text(
                    dict(row), kind, tz=tz, salon_name=row["salon_name"]
                ),
            })
    return ready


async def claim_reminder(
    pool: Any, booking_id: str, kind: str, *, status: str = "sent"
) -> bool:
    """Пометить «напоминание отправлено». ``False`` — уже отправляли.

    Возврат конкурентен: два инстанса worker'а получат True ровно по одному разу.
    Несуществующая запись тоже даёт ``False``, а не исключение: worker не должен
    падать из-за чужого или удалённого идентификатора.
    """
    try:
        await pool.execute(
            "INSERT INTO reminders (booking_id, kind, status) VALUES ($1, $2, $3)",
            booking_id, kind, status,
        )
    except Exception as exc:  # noqa: BLE001 — уникальный ключ = «уже отправляли»
        text = str(exc).lower()
        if "unique" in text or "foreign key" in text or "reminders_booking_id_fkey" in text:
            return False
        raise
    return True


async def mark_reminder_failed(pool: Any, booking_id: str, kind: str) -> None:
    """Отправка не удалась — записываем это, чтобы видеть в отчёте и не спамить."""
    await pool.execute(
        "INSERT INTO reminders (booking_id, kind, status) VALUES ($1, $2, 'failed') "
        "ON CONFLICT (booking_id, kind) DO UPDATE SET status = 'failed', sent_at = now()",
        booking_id, kind,
    )


async def reminders_stats(pool: Any, salon_id: str, *, since: datetime) -> dict[str, Any]:
    """Сколько напоминаний ушло и сколько провалилось — для отчёта об эффекте."""
    row = await pool.fetchrow(
        """
        SELECT count(*) FILTER (WHERE r.status = 'sent') AS sent,
               count(*) FILTER (WHERE r.status = 'failed') AS failed
        FROM reminders r JOIN bookings b ON b.id = r.booking_id
        WHERE b.salon_id = $1 AND r.sent_at >= $2
        """,
        salon_id, since,
    )
    return {"sent": int(row["sent"] or 0), "failed": int(row["failed"] or 0)}
