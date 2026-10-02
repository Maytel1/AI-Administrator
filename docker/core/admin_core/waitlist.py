"""Лист ожидания: освободившийся слот отдаём тому, кто его ждёт.

Смысл в деньгах: отменённая запись — это пустой слот. Если на него есть
очередь, слот возвращается в работу автоматически, а не пропадает.

Правила, чтобы не превратить это в спам:
* предложение получает **один** клиент — первый в очереди по времени;
* после предложения запись в листе закрывается (``active = false``);
* клиенту нельзя предложить то, что он сам и отменял (фильтр по ``client_id``);
* предложение помечается в ``reminders`` с ключом ``waitlist``, поэтому
  повторный запуск worker'а не пришлёт его дважды.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .reminders import claim_reminder, reminder_text


async def add_to_waitlist(
    pool: Any, salon_id: str, client_id: str, *, service_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Клиент встал в очередь на услугу (или на любое окно, если услуга не указана)."""
    row = await pool.fetchrow(
        "INSERT INTO waitlist (salon_id, client_id, service_id, note) "
        "VALUES ($1, $2, $3, $4) RETURNING id, created_at",
        salon_id, client_id, service_id, (note or "").strip() or None,
    )
    return {"ok": True, "id": str(row["id"]), "created_at": row["created_at"]}


async def waitlist_size(pool: Any, salon_id: str) -> int:
    return int(await pool.fetchval(
        "SELECT count(*) FROM waitlist WHERE salon_id = $1 AND active", salon_id
    ))


async def take_waitlist_offer(
    pool: Any, freed: dict[str, Any], *, tz: ZoneInfo, salon_name: str,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Кому предложить освободившийся слот. ``None`` — желающих нет.

    ``freed`` — строка записи (id, starts_at, ends_at, service_id, client_id).
    """
    rows = await pool.fetch(
        """
        SELECT w.id, w.client_id, w.service_id, c.phone, c.name,
               sv.title AS service_title, m.name AS master_name, sa.timezone
        FROM waitlist w
        JOIN clients c ON c.id = w.client_id
        JOIN salons sa ON sa.id = w.salon_id
        LEFT JOIN bookings b ON b.id = $1
        LEFT JOIN masters m ON m.id = b.master_id
        LEFT JOIN services sv ON sv.id = COALESCE(w.service_id, b.service_id)
        WHERE w.salon_id = $2 AND w.active
          AND (w.service_id IS NULL OR w.service_id = b.service_id)
          AND w.client_id <> b.client_id
        ORDER BY w.created_at
        FOR UPDATE OF w SKIP LOCKED
        LIMIT 1
        """,
        freed["id"], freed.get("salon_id"),
    )
    if not rows:
        return None
    row = rows[0]
    text = reminder_text(
        {
            "starts_at": freed["starts_at"],
            "service_title": row["service_title"],
            "master_name": row["master_name"],
        },
        "waitlist", tz=tz, salon_name=salon_name,
    )
    # Закрываем предложение до отправки: если отправка упадёт, клиент в очереди
    # уже не стоит, но это лучше, чем прислать ему предложение десять раз.
    await pool.execute(
        "UPDATE waitlist SET active = false, offered_at = now() WHERE id = $1", row["id"]
    )
    return {
        "client_id": str(row["client_id"]),
        "phone": row["phone"],
        "name": row["name"],
        "text": text,
        "booking_id": str(freed["id"]),
        "kind": "waitlist",
    }


async def claim_offer(pool: Any, booking_id: str) -> bool:
    """Пометить предложение отправленным (общий ключ с напоминаниями)."""
    return await claim_reminder(pool, booking_id, "waitlist")
