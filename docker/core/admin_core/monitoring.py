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
    from .crm.connectors import unavailable_salons  # noqa: PLC0415

    salons = [
        dict(row) for row in await pool.fetch(
            "SELECT id, name, crm_provider FROM salons WHERE is_active"
        )
    ]
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


def json_report(rows: list[dict[str, Any]]) -> str:
    """Метрики в JSON — для /metrics и внешних сборщиков."""
    return json.dumps(rows, ensure_ascii=False)
