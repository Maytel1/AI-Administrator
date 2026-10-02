"""Выгрузка отчётов в CSV и сводка по всем точкам.

CSV собирается без библиотек и **экранируется по правилам Excel/ODS**:
разделитель `;`, кавычки и переводы строк внутри значения — так открытый файл
не разваливается и формула в поле не превращается в исполняемый код.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

# Формулы в CSV — это код, который посчитает Excel. Значение, начинающееся с
# =, +, -, @, мы защищаем префиксом: файл из панели не должен уметь исполнять.
DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    if text.startswith(DANGEROUS_PREFIXES):
        text = "'" + text
    return text


def to_csv(headers: list[str], rows: list[list[Any]]) -> bytes:
    """CSV в UTF-8 с BOM: без него Excel открывает кириллицу как кракозябры."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_cell(cell) for cell in row])
    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")


async def export_daily(
    pool: Any, salon: dict[str, Any], start: datetime, end: datetime
) -> bytes:
    """Выгрузка «выручка по дням» — то, что владелец отдаёт бухгалтеру."""
    from .web.reports import revenue_by_day

    rows = await revenue_by_day(pool, str(salon["id"]), start, end)
    table = [
        [
            row["day"].isoformat() if hasattr(row["day"], "isoformat") else row["day"],
            row["bookings"], row["from_bot"],
            f'{float(row["revenue"] or 0):.2f}',
        ]
        for row in rows
    ]
    return to_csv(["Дата", "Записей", "Из бота", "Выручка, ₽"], table)


async def export_masters(
    pool: Any, salon: dict[str, Any], start: datetime, end: datetime
) -> bytes:
    """Выгрузка по мастерам: кто сколько работал и сколько принёс."""
    from .web.reports import load_by_master

    rows = await load_by_master(pool, str(salon["id"]), start, end)
    table = [
        [
            row["name"], row["bookings"],
            f'{float(row["busy_hours"] or 0):.2f}',
            f'{float(row["revenue"] or 0):.2f}',
        ]
        for row in rows
    ]
    return to_csv(["Мастер", "Записей", "Занято, ч", "Выручка, ₽"], table)


async def network_summary(
    pool: Any, *, days: int = 30, tz_name: str = "UTC"
) -> list[dict[str, Any]]:
    """Сводка по всем точкам — для роли «наша команда».

    Один запрос вместо N: у команды до двадцати точек, по очереди их обходить
    нельзя — это заметная задержка на каждом открытии сводки.
    """
    return [
        dict(row) for row in await pool.fetch(
            """
            SELECT s.name AS salon, s.plan,
                   count(b.id) AS bookings,
                   COALESCE(sum(sv.price), 0) AS revenue,
                   count(b.id) FILTER (WHERE b.status = 'no_show') AS no_shows,
                   (SELECT count(*) FROM clients c WHERE c.salon_id = s.id) AS clients
            FROM salons s
            LEFT JOIN bookings b ON b.salon_id = s.id
                 AND b.starts_at >= now() - make_interval(days => $1)
                 AND b.status IN ('booked','confirmed','completed')
            LEFT JOIN services sv ON sv.id = b.service_id
            WHERE s.is_active
            GROUP BY s.id, s.name, s.plan
            ORDER BY revenue DESC
            """,
            days,
        )
    ]


async def month_bounds(tz: ZoneInfo, *, today: datetime | None = None) -> tuple[datetime, datetime]:
    """Границы календарного месяца салона в UTC — для месячной выгрузки."""
    now = today or datetime.now(tz)
    start_local = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end_local = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return (
        start_local.astimezone(ZoneInfo("UTC")),
        end_local.astimezone(ZoneInfo("UTC")),
    )
