"""Отчёты для владельца точки: деньги, загрузка, работа бота.

Числа считаем по факту записей и диалогов, а не «по ощущениям»: владелец
должен видеть, сколько принес бот и сколько стоил каждый диалог.
Время — в таймзоне салона (её передаёт вызывающий через границы периода).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

# Периоды в панели: ключ в URL, подпись, длина в днях (None — «до конца месяца»).
PERIODS: tuple[tuple[str, str, int | None], ...] = (
    ("7", "7 дней", 7),
    ("30", "30 дней", 30),
    ("90", "90 дней", 90),
    ("month", "Этот месяц", None),
)
DEFAULT_PERIOD = "30"


@dataclass(frozen=True)
class Window:
    """Границы периода в UTC и его длина в днях."""

    key: str
    label: str
    start: datetime
    end: datetime

    @property
    def days(self) -> int:
        return max(1, round((self.end - self.start).total_seconds() / 86400))


def resolve_period(
    key: str | None, tz: ZoneInfo, *, today: datetime | None = None
) -> tuple[Window, Window]:
    """Разбирает период из URL и строит предыдущий такой же длины.

    Предыдущий период нужен владельцу не ради красоты: «выручка 320 тыс.» без
    сравнения — просто число. Он должен видеть, что месяц назад было 410 тыс.

    Границы считаем в таймзоне салона, наружу отдаём в UTC — так же, как в
    остальных запросах панели. Мусорный ключ молча становится 30 днями: битая
    ссылка не должна ронять раздел.
    """
    now = today or datetime.now(tz)
    chosen = next((p for p in PERIODS if p[0] == str(key or "")), None) or PERIODS[1]
    period_key, label, days = chosen
    if days is None:  # «этот месяц»: с 1-го числа по завтра
        local_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        local_start = (now - timedelta(days=days)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    local_end = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start = local_start.astimezone(ZoneInfo("UTC"))
    end = local_end.astimezone(ZoneInfo("UTC"))
    span = end - start
    current = Window(key=period_key, label=label, start=start, end=end)
    previous = Window(
        key=period_key,
        label=f"прошлый период ({current.days} дн.)",
        start=start - span,
        end=start,
    )
    return current, previous


def delta(current: float, previous: float) -> dict[str, Any]:
    """Изменение к прошлому периоду.

    Процент считаем только от ненулевой базы: «рост на 100 %» от нуля — это не
    информация, а ошибка деления, поэтому отдаём ``None`` и рисуем прочерк.
    """
    change = current - previous
    percent = round(100.0 * change / previous, 1) if previous else None
    return {"current": current, "previous": previous, "change": change, "percent": percent}


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

async def compare_periods(
    pool: Any, salon_id: str, current: Window, previous: Window
) -> dict[str, Any]:
    """Воронка и потери за оба периода плюс изменения между ними."""
    now = await funnel(pool, salon_id, current.start, current.end)
    before = await funnel(pool, salon_id, previous.start, previous.end)
    now_loss = await cancellations(pool, salon_id, current.start, current.end)
    before_loss = await cancellations(pool, salon_id, previous.start, previous.end)
    return {
        "current": {"funnel": now, "loss": now_loss, "window": current},
        "previous": {"funnel": before, "loss": before_loss, "window": previous},
        "diff": {
            "revenue": delta(now["revenue"], before["revenue"]),
            "bookings": delta(now["bookings"], before["bookings"]),
            "dialogs": delta(now["dialogs"], before["dialogs"]),
            "conversion": delta(now["conversion"], before["conversion"]),
            "avg_check": delta(now["avg_check"], before["avg_check"]),
            "lost_rate": delta(now_loss["lost_rate"], before_loss["lost_rate"]),
            "new_clients": delta(now["new_clients"], before["new_clients"]),
        },
    }
