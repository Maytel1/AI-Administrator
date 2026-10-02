"""Агрегаты дашборда «Сегодня» (фаза 4).

Считаются в таймзоне салона: границы суток передаются из ``queries.day_bounds``.
Все выборки изолированы по ``salon_id`` — мультиарендность обязательна.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


async def today_stats(pool: Any, salon_id: str, start: datetime, end: datetime) -> dict[str, Any]:
    """Плитки дашборда: записи, деньги, бот, диалоги, новые клиенты, загрузка."""
    row = await pool.fetchrow(
        """
        SELECT
            count(*) FILTER (WHERE b.status IN ('booked','confirmed','completed')) AS total,
            count(*) FILTER (WHERE b.status = 'confirmed')                           AS confirmed,
            count(*) FILTER (WHERE b.status = 'completed')                           AS completed,
            count(*) FILTER (WHERE b.status = 'cancelled')                           AS cancelled,
            count(*) FILTER (WHERE b.status = 'no_show')                             AS no_show,
            count(*) FILTER (WHERE b.source = 'bot')                                AS from_bot,
            COALESCE(sum(s.price) FILTER (
                WHERE b.status IN ('booked','confirmed','completed')), 0)            AS revenue
        FROM bookings b JOIN services s ON s.id = b.service_id
        WHERE b.salon_id = $1 AND b.starts_at >= $2 AND b.starts_at < $3
        """,
        salon_id, start, end,
    )
    dialogs = await pool.fetchval(
        "SELECT count(*) FROM dialogs WHERE salon_id = $1 "
        "AND started_at >= $2 AND started_at < $3",
        salon_id, start, end,
    )
    new_clients = await pool.fetchval(
        "SELECT count(*) FROM clients WHERE salon_id = $1 "
        "AND created_at >= $2 AND created_at < $3",
        salon_id, start, end,
    )
    week = await pool.fetchval(
        "SELECT count(*) FROM bookings WHERE salon_id = $1 "
        "AND status IN ('booked','confirmed') AND starts_at >= $2 AND starts_at < $3",
        salon_id, end, end + timedelta(days=7),
    )
    busy = await pool.fetchval(
        "SELECT count(DISTINCT master_id) FROM bookings WHERE salon_id = $1 "
        "AND starts_at >= $2 AND starts_at < $3 AND status IN ('booked','confirmed')",
        salon_id, start, end,
    )
    masters_total = await pool.fetchval(
        "SELECT count(*) FROM masters WHERE salon_id = $1 AND is_active", salon_id
    )
    stats = {k: (v or 0) for k, v in dict(row).items()}
    stats.update(
        dialogs=dialogs or 0,
        new_clients=new_clients or 0,
        week=week or 0,
        busy_masters=busy or 0,
        masters_total=masters_total or 0,
    )
    stats["revenue"] = float(stats["revenue"] or 0)
    return stats


async def schedule_for_day(
    pool: Any, salon_id: str, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Расписание суток: кто, к кому, во сколько — основа страницы «Сегодня»."""
    rows = await pool.fetch(
        """
        SELECT b.id, b.starts_at, b.ends_at, b.status, b.source, b.prepayment_status,
               c.name AS client_name, c.phone AS client_phone,
               m.name AS master_name, s.title AS service_title,
               s.duration_min, s.price
        FROM bookings b
        JOIN clients c ON c.id = b.client_id
        JOIN masters m ON m.id = b.master_id
        JOIN services s ON s.id = b.service_id
        WHERE b.salon_id = $1 AND b.starts_at >= $2 AND b.starts_at < $3
        ORDER BY b.starts_at, m.name
        """,
        salon_id, start, end,
    )
    return [dict(r) for r in rows]
