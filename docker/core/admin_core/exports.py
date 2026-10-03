"""Бухгалтерские выгрузки: реестр счетов, реестр клиентов, сводка по долгам.

Зачем именно такие: бухгалтерия просит не «красивые отчёты», а сверку —
что выставлено, что оплачено, кто должен и с какими реквизитами. Всё считается
одной функцией на SQL и отдаётся в CSV с защитой от формул.

Время в отчётах — в agreed-таймзоне БД (UTC+5), периоды календарные."""

import csv
import io
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

PAYMENT_STATUS_RU = {
    "issued": "выставлен", "paid": "оплачен",
    "overdue": "просрочен", "void": "аннулирован",
}
CLIENT_TYPE_RU = {"self_employed": "самозанятый", "ip": "ИП", "llc": "ООО"}

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


async def payments_registry(
    pool: Any, *, start: date | None = None, end: date | None = None,
    salon_id: str | None = None,
) -> dict[str, Any]:
    """Реестр выставленных счетов за период: что, кому и на сколько.

    Основной запрос бухгалтерии: свести выставленное с оплаченным и найти
    расхождения по конкретным салонам.
    """
    # Период по умолчанию — текущий календарный месяц в agreed-таймзоне БД.
    today = start or date.today()
    month_start = date(today.year, today.month, 1)
    month_end = (
        date(today.year + 1, 1, 1) if month_start.month == 12
        else date(today.year, month_start.month + 1, 1)
    )
    params: list[Any] = [
        datetime.combine(start or month_start, datetime.min.time()),
        datetime.combine(end or month_end, datetime.min.time()),
    ]
    where_salon = ""
    if salon_id:
        params.append(salon_id)
        where_salon = f" AND p.salon_id = ${len(params)}"
    rows = await pool.fetch(
        "SELECT p.invoice_no, p.period_start, p.period_end, p.plan, p.amount, "
        "p.method, p.status, p.issued_at, p.paid_at, p.note, "
        "s.name AS salon_name, s.id AS salon_id, "
        "COALESCE(l.client_type, '') AS client_type, "
        "COALESCE(l.legal_name, s.name) AS legal_name, COALESCE(l.inn, '') AS inn "
        "FROM payments p JOIN salons s ON s.id = p.salon_id "
        "LEFT JOIN client_legal l ON l.salon_id = p.salon_id "
        "WHERE p.period_start >= $1 AND p.period_start < $2" + where_salon +
        " ORDER BY s.name, p.period_start",
        *params,
    )
    registry = [dict(row) for row in rows]
    issued = sum(float(r["amount"] or 0) for r in registry)
    paid = sum(float(r["amount"] or 0) for r in registry if r["status"] == "paid")
    return {
        "rows": registry,
        "totals": {
            "count": len(registry), "issued": issued, "paid": paid,
            "debt": issued - paid,
        },
        "period": (
            f"{month_start:%d.%m.%Y} — {(month_end - timedelta(days=1)):%d.%m.%Y}"
        ),
    }


def payments_csv(registry: dict[str, Any]) -> bytes:
    """CSV реестра счетов."""
    header = [
        "Счёт", "Период с", "Период по", "Тариф", "Плательщик", "Тип", "ИНН",
        "Сумма", "Способ", "Статус", "Выставлен", "Оплачен", "Примечание",
    ]
    rows = [
        [
            row["invoice_no"], row["period_start"], row["period_end"], row["plan"],
            row["legal_name"],
            CLIENT_TYPE_RU.get(str(row["client_type"]), "—"), row["inn"],
            f"{float(row['amount'] or 0):.2f}", row["method"],
            PAYMENT_STATUS_RU.get(str(row["status"]), row["status"]),
            row["issued_at"], row["paid_at"] or "", row["note"] or "",
        ]
        for row in registry["rows"]
    ]
    return to_csv(header, rows)


async def debtors(pool: Any, *, as_of: date | None = None) -> list[dict[str, Any]]:
    """Кто и сколько должен: платные салоны с неоплаченными счетами."""
    day = as_of or date.today()
    rows = await pool.fetch(
        "SELECT s.id, s.name, s.plan, s.paid_until, "
        "count(p.id) AS unpaid, coalesce(sum(p.amount), 0) AS debt, "
        "max(p.period_end) AS oldest_due, "
        "COALESCE(l.client_type, '') AS client_type, "
        "COALESCE(l.legal_name, s.name) AS legal_name, "
        "COALESCE(l.inn, '') AS inn "
        "FROM salons s LEFT JOIN client_legal l ON l.salon_id = s.id "
        "LEFT JOIN payments p ON p.salon_id = s.id AND p.status = 'issued' "
        "AND p.period_end < $1 "
        "WHERE s.is_active AND s.plan <> 'trial' "
        "GROUP BY s.id, s.name, s.plan, s.paid_until, l.client_type, "
        "l.legal_name, l.inn "
        "HAVING count(p.id) > 0 OR s.paid_until IS NULL OR s.paid_until < $1 "
        "ORDER BY debt DESC, s.name",
        day,
    )
    return [dict(row) for row in rows]


async def clients_registry(
    pool: Any, *, salon_id: str | None = None
) -> list[dict[str, Any]]:
    """Реестр клиентов салона с телефоном и статистикой визитов."""
    params: list[Any] = []
    where = ""
    if salon_id:
        params.append(salon_id)
        where = " AND c.salon_id = $1"
    rows = await pool.fetch(
        "SELECT c.phone, c.name, c.notes, c.created_at, c.requires_prepayment, "
        "s.name AS salon_name, s.id AS salon_id, "
        "count(b.id) FILTER (WHERE b.status IN "
        "  ('booked','confirmed','completed')) AS visits, "
        "count(b.id) FILTER (WHERE b.status = 'no_show') AS no_shows, "
        "max(b.starts_at) AS last_visit "
        "FROM clients c JOIN salons s ON s.id = c.salon_id "
        "LEFT JOIN bookings b ON b.client_id = c.id "
        "WHERE 1 = 1" + where +
        " GROUP BY c.id, c.phone, c.name, c.notes, c.created_at, "
        "c.requires_prepayment, s.name, s.id "
        " ORDER BY c.created_at DESC",
        *params,
    )
    return [dict(row) for row in rows]


def clients_csv(clients: list[dict[str, Any]]) -> bytes:
    """CSV реестра клиентов."""
    header = [
        "Салон", "Телефон", "Клиент", "Заметки", "Визиты", "Неявки",
        "Последний визит", "Предоплата", "Создан",
    ]
    rows = [[
        row["salon_name"], row["phone"], row["name"] or "", row["notes"] or "",
        row["visits"], row["no_shows"], row["last_visit"] or "",
        "да" if row["requires_prepayment"] else "нет", row["created_at"],
    ] for row in clients]
    return to_csv(header, rows)
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
