"""Наблюдаемость: что должно вызвать тревогу, а не остаться в логах.

Панель — это отчётность «после». Мониторинг отвечает на другое: «что сломалось
сейчас». Здесь только решения — что считать проблемой и как это сформулировать;
отправка в Telegram живёт в worker'е.

Повторы ограничены таблицей alerts: одно и то же событие не должно слать
сообщение каждую минуту, но при этом должно переслать его снова, если не
помогли (например, сервис так и не поднялся).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

ALERT_COOLDOWN = timedelta(hours=1)


@dataclass(frozen=True)
class Alert:
    """Тревога: короткий ключ (для дедупликации) и текст для человека."""

    kind: str
    text: str
    severity: str = "warning"  # warning | critical


async def should_send(
    pool: Any, kind: str, *, now: datetime | None = None, cooldown: timedelta | None = None
) -> bool:
    """Не слать ли это событие снова. Второй прогон подряд — молчим."""
    moment = now or datetime.now(ZoneInfo("UTC"))
    limit = cooldown or ALERT_COOLDOWN
    # Предыдущее значение снимаем ДО обновления: если читать его в RETURNING,
    # подзапрос видит уже обновлённую строку и тревога не отправится никогда.
    row = await pool.fetchrow(
        "WITH prev AS (SELECT last_sent FROM alerts WHERE kind = $1) "
        "INSERT INTO alerts (kind, text, last_sent, times) VALUES ($1, '', $2, 1) "
        "ON CONFLICT (kind) DO UPDATE SET last_sent = $2, times = alerts.times + 1 "
        "RETURNING (SELECT last_sent FROM prev) AS previous",
        kind, moment,
    )
    previous = row["previous"] if row else None
    return previous is None or (moment - previous) >= limit


async def remember_alert(pool: Any, kind: str, text: str) -> None:
    """Сохранить текст последней тревоги — он попадёт в лог и в отчёт."""
    await pool.execute(
        "INSERT INTO alerts (kind, text) VALUES ($1, $2) "
        "ON CONFLICT (kind) DO UPDATE SET text = $2",
        kind, text,
    )


async def log_login(
    pool: Any, *, login: str, ip: str, ok: bool, reason: str = "",
    user_agent: str = "",
) -> None:
    """Записать попытку входа. Логин обрезаем: это внешние данные."""
    await pool.execute(
        "INSERT INTO login_log (login, ip, ok, reason, user_agent) "
        "VALUES ($1, $2, $3, $4, $5)",
        (login or "")[:200], (ip or "")[:64], ok, (reason or "")[:100],
        (user_agent or "")[:200],
    )


async def login_failures_since(pool: Any, *, since: datetime) -> dict[str, Any]:
    """Сколько неудачных входов и сколько разных IP — признак перебора."""
    row = await pool.fetchrow(
        "SELECT count(*) AS fails, count(DISTINCT ip) AS ips "
        "FROM login_log WHERE NOT ok AND ts >= $1",
        since,
    )
    return {"fails": int(row["fails"] or 0), "ips": int(row["ips"] or 0)}


async def _revenue_alerts(pool: Any, salons: list[dict[str, Any]]) -> list[Alert]:
    """Тревоги о падении выручки: сравниваем недели попарно.

    Недельный срез выбран потому, что месяц к месяцу слишком шумно из-за
    отпусков и праздников — сработало бы кричащее «упало», а потом ложное
    «всё выросло».
    """
    found: list[Alert] = []
    for salon in salons:
        drop = await pool.fetchrow(
            """
            WITH last AS (
                SELECT COALESCE(sum(sv.price), 0) AS revenue FROM bookings b
                JOIN services sv ON sv.id = b.service_id
                WHERE b.salon_id = $1 AND b.starts_at >= now() - interval '7 days'
                  AND b.status IN ('booked','confirmed','completed')
            ), prev AS (
                SELECT COALESCE(sum(sv.price), 0) AS revenue FROM bookings b
                JOIN services sv ON sv.id = b.service_id
                WHERE b.salon_id = $1 AND b.starts_at >= now() - interval '14 days'
                  AND b.starts_at < now() - interval '7 days'
                  AND b.status IN ('booked','confirmed','completed')
            )
            SELECT last.revenue AS last_week, prev.revenue AS prev_week
            FROM last, prev
            """,
            salon["id"],
        )
        previous = float(drop["prev_week"] or 0) if drop else 0.0
        if previous <= 0:      # сравнивать не с чем: пустая неделя — не просадка
            continue
        change = (float(drop["last_week"] or 0) - previous) / previous
        if change <= -0.3:     # упали на треть и больше
            found.append(Alert(
                f"revenue_drop:{salon['id']}",
                f"Выручка «{salon['name']}» упала на {abs(round(change * 100))}% "
                "к прошлой неделе — стоит посмотреть, что сломалось",
                "critical",
            ))
    return found


async def check_alerts(pool: Any, *, now: datetime | None = None) -> list[Alert]:
    """Проверяет всё, что умеет проверять без доступа к железу.

    Пороги берём с запасом: лучше лишняя тревога, чем пропущенная недоступность.
    """
    moment = now or datetime.now(ZoneInfo("UTC"))
    found: list[Alert] = []

    stale = await pool.fetchrow(
        "SELECT count(*) FROM bookings b JOIN salons s ON s.id = b.salon_id "
        "WHERE s.is_active AND b.source = 'bot' AND b.starts_at < now() - interval '3 hours' "
        "AND b.status = 'booked'"
    )
    if int(stale["count"]) > 0:
        found.append(Alert(
            "bookings_stale",
            f"{stale['count']} записей не переведены в «выполнена» три часа спустя — "
            "бот может не отвечать",
            "critical",
        ))

    unpaid = await pool.fetchrow(
        "SELECT count(*) AS cnt, coalesce(sum(p.amount), 0) AS total FROM payments p "
        "JOIN salons s ON s.id = p.salon_id "
        "WHERE s.is_active AND s.plan <> 'trial' AND p.status = 'issued' "
        "AND p.period_end < CURRENT_DATE"
    )
    if int(unpaid["cnt"]) > 0:
        found.append(Alert(
            "payments_overdue",
            f"{unpaid['cnt']} счетов не оплачено на сумму "
            f"{float(unpaid['total'] or 0):.0f} руб. — /billing",
            "warning",
        ))

    attacks = await login_failures_since(pool, since=moment - timedelta(hours=1))
    if attacks["fails"] >= 20:
        found.append(Alert(
            "login_attacks",
            f"{attacks['fails']} неудачных входов за час с {attacks['ips']} адресов — "
            "похоже на перебор паролей",
            "critical",
        ))

    trial = await pool.fetchrow(
        "SELECT name, plan FROM salons "
        "WHERE is_active AND plan = 'trial' AND created_at < now() - interval '10 days'"
    )
    if trial:
        found.append(Alert(
            "trial_ending",
            f"Точка «{trial['name']}» на триале уже больше 10 дней — пора обсудить переход",
        ))

    unsent = await pool.fetchrow(
        "SELECT count(*) FROM reminders WHERE status = 'failed' AND sent_at > now() - interval '1 day'"
    )
    if int(unsent["count"]) >= 10:
        found.append(Alert(
            "reminders_failed",
            f"{unsent['count']} напоминаний не отправилось за сутки — "
            "проверьте токен бота и лимиты Telegram",
            "critical",
        ))

    # Точка может быть переведена на внешнюю CRM без ключей: тогда бот не сможет
    # ни показать слоты, ни записать клиента — и никто об этом не узнает.
    salons = [
        dict(row) for row in await pool.fetch(
            "SELECT id, name, crm_provider FROM salons WHERE is_active"
        )
    ]
    found.extend(await _revenue_alerts(pool, salons))

    from .crm.connectors import unavailable_salons  # noqa: PLC0415

    for salon in unavailable_salons(salons):
        found.append(Alert(
            f"crm_not_ready:{salon['salon_id']}",
            f"Точка «{salon['salon']}» настроена на {salon['label']}, "
            f"но не хватает переменных: {', '.join(salon['missing'])} — "
            "запись клиентов сейчас не работает",
            "critical",
        ))
    return found


def render_alert(alert: Alert, *, salon: str | None = None) -> str:
    """Текст сообщения в Telegram: коротко, без технического шума."""
    mark = "🔴" if alert.severity == "critical" else "🟡"
    prefix = f"{mark} {salon}: " if salon else f"{mark} "
    return f"{prefix}{alert.text}"


async def forget_client(
    pool: Any, salon_id: str, client_id: str, *, actor: str = "system:request",
) -> dict[str, Any]:
    """Удаление клиента по запросу (ст. 3-ФЗ о персональных данных).

    Что происходит: карточка обезличивается, история записей остаётся, но без
    связи с человеком. Полное удаление строк ломает отчётность и учёт налогов,
    поэтому ПДн стираются, а финансовый след — нет.
    """
    before = await pool.fetchrow(
        "SELECT phone, name, notes FROM clients WHERE id = $1 AND salon_id = $2",
        client_id, salon_id,
    )
    if not before:
        return {"ok": False, "error": "not_found"}
    await pool.execute(
        "UPDATE clients SET phone = $3, name = NULL, notes = NULL, "
        "requires_prepayment = false WHERE id = $1 AND salon_id = $2",
        client_id, salon_id, f"erased-{str(client_id)[:8]}",
    )
    # Диалоги — тоже ПДн: оставляем факт, убираем содержание.
    await pool.execute(
        "UPDATE dialog_messages SET content = '[удалено по запросу субъекта ПДн]' "
        "WHERE dialog_id IN (SELECT id FROM dialogs WHERE client_id = $1)",
        client_id,
    )
    await pool.execute(
        "INSERT INTO audit_log (salon_id, actor, action, details) "
        "VALUES ($1, $2, 'forget_client', $3)",
        salon_id, actor,
        json.dumps({"client_id": client_id, "phone_last4": (before["phone"] or "")[-2:]},
                   ensure_ascii=False),
    )
    return {"ok": True, "client_id": client_id}


def json_report(rows: list[dict[str, Any]]) -> str:
    """Метрики в JSON — для /metrics и внешних сборщиков."""
    return json.dumps(rows, ensure_ascii=False)
