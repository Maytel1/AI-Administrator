"""Тарифы и лимиты точек (salons.plan).

Поле ``plan`` в базе было, но его никто не проверял: пакет можно было «купить»,
и ничего не менялось. Здесь — единственное место, где лимиты описаны, и
единственное место, где они проверяются.

Логика простая и честная: сначала считаем, что уже есть (факт из таблиц),
потом сравниваем с планом. Никакой отдельной «счётчиков подписки», которая
может разойтись с реальностью: source of truth — сами записи.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TRIAL_DAYS = 14

# Порог предупреждения об исчерпании лимита: при 80% использования ресурса
# панель показывает предупреждение — чтобы смену тарифа успели выбрать заранее.
WARN_PERCENT = 80

# Русские названия ресурсов для сообщений и индикаторов.
RESOURCE_NAMES = {
    "clients": ("клиентских карточек", "карточки клиентов"),
    "services": ("услуг в прайсе", "услуги в прайсе"),
    "masters": ("мастеров", "мастера"),
    "bookings_per_month": ("записей в этом месяце", "записи в этом месяце"),
}

# Лимиты тарифа: None — без ограничения.
# plan            клиенты услуги мастера записи_в_месяц
PLAN_LIMITS: dict[str, dict[str, int | None]] = {
    "trial": {"clients": 20, "services": 5, "masters": 2, "bookings_per_month": 50},
    "start": {"clients": 300, "services": 30, "masters": 10, "bookings_per_month": 600},
    "standard": {"clients": 2000, "services": 150, "masters": 40,
                 "bookings_per_month": 5000},
    "pro": {"clients": None, "services": None, "masters": None, "bookings_per_month": None},
    "network": {"clients": None, "services": None, "masters": None,
                "bookings_per_month": None},
}
DEFAULT_PLAN = "start"


@dataclass(frozen=True)
class Usage:
    """Сколько уже занято: всё считается из таблиц, а не из счётчиков."""

    clients: int = 0
    services: int = 0
    masters: int = 0
    bookings_this_month: int = 0
    trial_ends_at: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


# Имя поля Usage может отличаться от ключа ресурса в тарифе: так пишется
# «кто где используется». Явная карта лучше, чем соглашение по именам.
USAGE_FIELD = {
    "clients": "clients",
    "services": "services",
    "masters": "masters",
    "bookings_per_month": "bookings_this_month",
}


def limit_of(plan: str, resource: str) -> int | None:
    """Лимит ресурса по тарифу; None — без ограничения."""
    return PLAN_LIMITS.get(plan or DEFAULT_PLAN, PLAN_LIMITS[DEFAULT_PLAN]).get(resource)


async def current_usage(pool: Any, salon_id: str, *, tz_name: str = "UTC") -> Usage:
    """Фактическое использование ресурсов точки."""
    row = await pool.fetchrow(
        """
        SELECT
            (SELECT count(*) FROM clients WHERE salon_id = $1) AS clients,
            (SELECT count(*) FROM services WHERE salon_id = $1 AND is_active) AS services,
            (SELECT count(*) FROM masters WHERE salon_id = $1 AND is_active) AS masters,
            (SELECT count(*) FROM bookings
              WHERE salon_id = $1
                AND starts_at >= date_trunc('month', now() AT TIME ZONE $2) AT TIME ZONE $2
                AND status IN ('booked','confirmed','completed')) AS bookings_this_month
        """,
        salon_id, tz_name,
    )
    return Usage(
        clients=int(row["clients"]),
        services=int(row["services"]),
        masters=int(row["masters"]),
        bookings_this_month=int(row["bookings_this_month"]),
    )


async def check_limit(
    pool: Any, salon: dict[str, Any], resource: str, *, adding: int = 1,
    tz_name: str = "UTC",
) -> dict[str, Any]:
    """Хватает ли тарифа на ещё ``adding`` единиц.

    Возвращает ``{"ok": bool, ...}``: ``ok=False`` означает «не хватает»,
    ``message`` объясняет человеку, что именно и сколько можно.
    """
    plan = str(salon.get("plan") or DEFAULT_PLAN)
    limit = limit_of(plan, resource)
    if limit is None:
        return {"ok": True, "plan": plan, "limit": None, "used": None}
    usage = await current_usage(pool, str(salon["id"]), tz_name=tz_name)
    used = getattr(usage, USAGE_FIELD.get(resource, resource), 0)
    if used + adding <= limit:
        return {"ok": True, "plan": plan, "limit": limit, "used": used}
    names = {
        "clients": ("клиентских карточек", "карточки клиентов"),
        "services": ("услуг в прайсе", "услуги в прайсе"),
        "masters": ("мастеров", "мастера"),
        "bookings_per_month": ("записей в этом месяце", "записи в этом месяце"),
    }
    one, many = names.get(resource, (resource, resource))
    return {
        "ok": False,
        "plan": plan,
        "limit": limit,
        "used": used,
        "error": "plan_limit",
        "message": (
            f"На тарифе «{plan}» допустимо {limit} — уже {used}. "
            f"Лимит на {one}: {many} больше не добавить, нужен следующий тариф."
        ),
    }


def percent_of(used: int, limit: int | None) -> int | None:
    """Процент использования лимита; None — лимита нет (без ограничения).

    Деление на ноль исключено: limit=0 считается «без ограничения» — так
    сломанный env не блокирует всю панель.
    """
    if not limit:
        return None
    return min(999, int(round(100 * int(used) / int(limit))))


async def usage_report(pool: Any, salon: dict[str, Any]) -> dict[str, Any]:
    """Полная картина «сколько занято / сколько можно» — для панели и алертов.

    В каждой строке: used, limit, percent (None если лимита нет) и warn —
    True при достижении WARN_PERCENT (80%). ``near_limit`` — ресурсы на
    пороге, ``at_limit`` — исчерпанные: по ним панель строит предупреждение.
    """
    plan = str(salon.get("plan") or DEFAULT_PLAN)
    usage = await current_usage(
        pool, str(salon["id"]), tz_name=str(salon.get("timezone") or "UTC")
    )
    rows: list[dict[str, Any]] = []
    near_limit: list[str] = []
    exhausted: list[str] = []
    for resource in ("clients", "services", "masters", "bookings_per_month"):
        used = int(getattr(usage, USAGE_FIELD.get(resource, resource), 0))
        limit = limit_of(plan, resource)
        percent = percent_of(used, limit)
        warn = percent is not None and percent >= WARN_PERCENT
        rows.append({
            "resource": resource,
            "used": used,
            "limit": limit,
            "percent": percent,
            "warn": warn,
        })
        if limit is None:
            continue
        if used >= int(limit):
            exhausted.append(resource)
        elif warn:
            near_limit.append(resource)
    return {
        "plan": plan,
        "trial_ends_at": salon.get("trial_ends_at"),
        "rows": rows,
        "near_limit": near_limit,
        "at_limit": exhausted,
    }
