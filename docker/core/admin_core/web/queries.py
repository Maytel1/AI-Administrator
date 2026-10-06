"""Справочники и списки разделов веб-панели.

Изоляция по ``salon_id`` обязательна в каждом запросе — мультиарендность
(DEVELOPMENT_PLAN.md, фаза 4). Подписи статусов/каналов отдаём отсюда же,
чтобы UI не знал про коды в БД.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

UTC = ZoneInfo("UTC")

STATUS_RU = {
    "booked": "Записан",
    "confirmed": "Подтверждён",
    "completed": "Выполнен",
    "cancelled": "Отменён",
    "no_show": "Не пришёл",
}
STATUS_TONE = {
    "booked": "accent",
    "confirmed": "green",
    "completed": "gray",
    "cancelled": "red",
    "no_show": "red",
}
CHANNEL_RU = {
    "voice": "Голос",
    "telegram": "Telegram",
    "whatsapp": "WhatsApp",
    "vk": "ВКонтакте",
    "cli": "CLI",
}
SOURCE_RU = {"bot": "бот", "manual": "вручную", "crm_sync": "из CRM"}
PREPAY_RU = {
    "not_required": "не нужна",
    "offered": "предложена",
    "paid": "оплачена",
    "declined": "отказался",
}
PLAN_RU = {
    "trial": "Пробный",
    "start": "Старт",
    "standard": "Стандарт",
    "pro": "Про",
    "network": "Сеть",
}
WEEKDAY_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


async def salon_tz(pool: Any, salon_id: str) -> ZoneInfo:
    """Таймзона салона; при недоступности — Asia/Yekaterinburg (Тюмень, UTC+5)."""
    name = await pool.fetchval("SELECT timezone FROM salons WHERE id = $1", salon_id)
    return ZoneInfo(name or "Asia/Yekaterinburg")


def day_bounds(tz: ZoneInfo, when: datetime | None = None) -> tuple[datetime, datetime]:
    """Границы суток [начало, полночь) в UTC — для ``starts_at >= … AND < …``."""
    local = (when or datetime.now(tz)).astimezone(tz)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


async def list_salons(pool: Any) -> list[dict[str, Any]]:
    """Активные салоны (тенанты) — переключатель в шапке."""
    rows = await pool.fetch(
        "SELECT id, name, plan, crm_provider, timezone FROM salons "
        "WHERE is_active ORDER BY created_at"
    )
    return [dict(r) for r in rows]


async def bookings_between(
    pool: Any, salon_id: str, start: datetime, end: datetime, status: str = ""
) -> list[dict[str, Any]]:
    """Записи за период; ``status=''`` — без фильтра."""
    rows = await pool.fetch(
        """
        SELECT b.id, b.starts_at, b.ends_at, b.status, b.source, b.prepayment_status,
               c.name AS client_name, c.phone AS client_phone,
               m.name AS master_name, s.title AS service_title, s.price
        FROM bookings b
        JOIN clients c ON c.id = b.client_id
        JOIN masters m ON m.id = b.master_id
        JOIN services s ON s.id = b.service_id
        WHERE b.salon_id = $1 AND b.starts_at >= $2 AND b.starts_at < $3
          AND ($4 = '' OR b.status = $4)
        ORDER BY b.starts_at DESC
        """,
        salon_id, start, end, status,
    )
    return [dict(r) for r in rows]
async def clients_list(pool: Any, salon_id: str) -> list[dict[str, Any]]:
    """Клиенты точки: визиты, последний визит, требует ли предоплату."""
    rows = await pool.fetch(
        """
        SELECT c.id, c.phone, c.name, c.notes, c.requires_prepayment, c.created_at,
               count(b.id) FILTER (WHERE b.status = 'completed')                AS completed_visits,
               count(b.id) FILTER (
                   WHERE b.status IN ('booked','confirmed') AND b.starts_at >= now()
               )                                                                 AS upcoming,
               max(b.starts_at)                                                AS last_visit
        FROM clients c LEFT JOIN bookings b ON b.client_id = c.id
        WHERE c.salon_id = $1
        GROUP BY c.id
        ORDER BY max(b.starts_at) DESC NULLS LAST, c.created_at DESC
        """,
        salon_id,
    )
    return [dict(r) for r in rows]


async def services_list(pool: Any, salon_id: str) -> list[dict[str, Any]]:
    """Прайс точки (то, что бот озвучивает клиенту)."""
    rows = await pool.fetch(
        "SELECT id, title, duration_min, price, is_active FROM services "
        "WHERE salon_id = $1 ORDER BY title",
        salon_id,
    )
    return [dict(r) for r in rows]


async def masters_list(pool: Any, salon_id: str) -> list[dict[str, Any]]:
    """Мастера с их графиком — из него нарезаются слоты для записей.

    Заодно тянем список услуг мастера (json): по нему на плитке видно, что
    человек делает, а в окне правки услуги отмечены галочками.
    """
    rows = await pool.fetch(
        """
        SELECT m.id, m.name, m.is_active,
               COALESCE(json_agg(json_build_object(
                   'weekday', w.weekday, 'start', w.start_time::text, 'end', w.end_time::text
               ) ORDER BY w.weekday) FILTER (WHERE w.weekday IS NOT NULL), '[]') AS hours,
               (SELECT count(*) FROM services s
                  JOIN master_services ms ON ms.service_id = s.id
                 WHERE ms.master_id = m.id)                                       AS services_count,
               (SELECT json_agg(json_build_object(
                       'id', s.id, 'title', s.title, 'active', s.is_active
                    ) ORDER BY s.title)
                  FROM services s
                  JOIN master_services ms ON ms.service_id = s.id
                 WHERE ms.master_id = m.id)                                       AS services
        FROM masters m LEFT JOIN working_hours w ON w.master_id = m.id
        WHERE m.salon_id = $1
        GROUP BY m.id ORDER BY m.name
        """,
        salon_id,
    )
    return [dict(r) for r in rows]


async def dialogs_list(pool: Any, salon_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Последние диалоги бота: наш главный актив — видно, чему он научился отвечать."""
    rows = await pool.fetch(
        """
        SELECT d.id, d.channel, d.started_at, d.outcome, d.llm_model, d.llm_cost,
               c.name AS client_name, c.phone AS client_phone,
               (SELECT count(*) FROM dialog_messages m WHERE m.dialog_id = d.id) AS messages
        FROM dialogs d LEFT JOIN clients c ON c.id = d.client_id
        WHERE d.salon_id = $1 ORDER BY d.started_at DESC LIMIT $2
        """,
        salon_id, limit,
    )
    return [dict(r) for r in rows]


async def dialog_messages(pool: Any, dialog_id: str) -> list[dict[str, Any]]:
    """Переписка конкретного диалога (роль → реплика)."""
    rows = await pool.fetch(
        "SELECT role, content, created_at FROM dialog_messages "
        "WHERE dialog_id = $1 ORDER BY id",
        dialog_id,
    )
    return [dict(r) for r in rows]


async def audit_list(pool: Any, salon_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Журнал аудита — основа для споров (LIABILITY_OPTIONS.md)."""
    rows = await pool.fetch(
        "SELECT id, ts, actor, action, details FROM audit_log "
        "WHERE salon_id = $1 ORDER BY ts DESC LIMIT $2",
        salon_id, limit,
    )
    return [dict(r) for r in rows]
