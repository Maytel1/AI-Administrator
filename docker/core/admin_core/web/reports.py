"""Отчёты для владельца точки: деньги, загрузка, работа бота.

Числа считаем по факту записей и диалогов, а не «по ощущениям»: владелец
должен видеть, сколько принес бот и сколько стоил каждый диалог.
Время — в таймзоне салона (её передаёт вызывающий через границы периода).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


async def revenue_by_day(
    pool: Any, salon_id: str, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Выручка и количество записей по дням — основной график отчёта.

    Считаем только состоявшиеся и активные записи: отменённые не приносят денег.
    """
    rows = await pool.fetch(
        """
        SELECT (b.starts_at AT TIME ZONE s.timezone)::date AS day,
               count(*) AS bookings,
               COALESCE(sum(sv.price), 0) AS revenue,
               count(*) FILTER (WHERE b.source = 'bot') AS from_bot
        FROM bookings b
        JOIN services sv ON sv.id = b.service_id
        JOIN salons s ON s.id = b.salon_id
        WHERE b.salon_id = $1 AND b.starts_at >= $2 AND b.starts_at < $3
          AND b.status IN ('booked','confirmed','completed')
        GROUP BY 1 ORDER BY 1
        """,
        salon_id, start, end,
    )
    return [dict(r) for r in rows]


async def load_by_master(
    pool: Any, salon_id: str, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Загрузка мастеров: сколько часов занято и сколько это принесло."""
    return [
        dict(r) for r in await pool.fetch(
            """
            SELECT m.name,
                   count(b.id) AS bookings,
                   COALESCE(sum(EXTRACT(EPOCH FROM (b.ends_at - b.starts_at)) / 3600), 0)
                       AS busy_hours,
                   COALESCE(sum(sv.price), 0) AS revenue
            FROM masters m
            LEFT JOIN bookings b ON b.master_id = m.id
                 AND b.starts_at >= $2 AND b.starts_at < $3
                 AND b.status IN ('booked','confirmed','completed')
            LEFT JOIN services sv ON sv.id = b.service_id
            WHERE m.salon_id = $1
            GROUP BY m.id, m.name ORDER BY revenue DESC
            """,
            salon_id, start, end,
        )
    ]


async def funnel(
    pool: Any, salon_id: str, start: datetime, end: datetime
) -> dict[str, Any]:
    """Воронка: диалоги → записи → состоявшиеся, плюс средний чек и стоимость диалога.

    Конверсия бота — главный вопрос прода: сколько усилий он даёт.
    """
    row = await pool.fetchrow(
        """
        SELECT
            (SELECT count(*) FROM dialogs
              WHERE salon_id = $1 AND started_at >= $2 AND started_at < $3) AS dialogs,
            (SELECT count(*) FROM bookings
              WHERE salon_id = $1 AND starts_at >= $2 AND starts_at < $3
                AND status IN ('booked','confirmed','completed')) AS bookings,
            (SELECT count(*) FROM bookings
              WHERE salon_id = $1 AND starts_at >= $2 AND starts_at < $3
                AND status = 'completed') AS completed,
            (SELECT COALESCE(sum(sv.price), 0) FROM bookings b
               JOIN services sv ON sv.id = b.service_id
              WHERE b.salon_id = $1 AND b.starts_at >= $2 AND b.starts_at < $3
                AND b.status IN ('booked','confirmed','completed')) AS revenue,
            (SELECT COALESCE(sum(llm_cost), 0) FROM dialogs
              WHERE salon_id = $1 AND started_at >= $2 AND started_at < $3) AS llm_cost,
            (SELECT count(*) FROM clients
              WHERE salon_id = $1 AND created_at >= $2 AND created_at < $3) AS new_clients
        """,
        salon_id, start, end,
    )
    stats = {k: (v or 0) for k, v in dict(row).items()}
    stats["revenue"] = float(stats["revenue"])
    stats["llm_cost"] = float(stats["llm_cost"])
    stats["conversion"] = round(
        100 * stats["bookings"] / stats["dialogs"] if stats["dialogs"] else 0.0, 1
    )
    stats["avg_check"] = (
        stats["revenue"] / stats["bookings"] if stats["bookings"] else 0.0
    )
    return stats


async def cancellations(
    pool: Any, salon_id: str, start: datetime, end: datetime
) -> dict[str, Any]:
    """Отмены и неявки — сколько записей теряем и почему."""
    row = await pool.fetchrow(
        """
        SELECT count(*) FILTER (WHERE status = 'cancelled') AS cancelled,
               count(*) FILTER (WHERE status = 'no_show') AS no_show,
               count(*) AS total
        FROM bookings WHERE salon_id = $1 AND starts_at >= $2 AND starts_at < $3
        """,
        salon_id, start, end,
    )
    data = {k: (v or 0) for k, v in dict(row).items()}
    data["lost_rate"] = round(
        100 * (data["cancelled"] + data["no_show"]) / data["total"]
        if data["total"] else 0.0, 1
    )
    return data
