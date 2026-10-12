"""Чек-лист онбординга: «первые шаги» нового владельца точки.

Показывает на главной, что уже настроено и что осталось, и исчезает сам,
когда всё готово. Считаем из таблиц (source of truth — сами записи), а не из
отдельного «флага онбординга»: иначе чек-лист разошёлся бы с реальностью —
удалили услугу, а галочка «услуги добавлены» осталась.
"""

from __future__ import annotations

from typing import Any

from .render import card, esc

# Шаги онбординга в порядке показа. done считаем по таблицам: ключ запроса →
# подпись и куда ведём за действием. count — для подсказки «добавлено N».
STEPS: tuple[dict[str, Any], ...] = (
    {
        "key": "services",
        "title": "Добавьте услуги в прайс",
        "hint": "Бот предлагает их клиентам с ценой и длительностью",
        "link": "/services",
    },
    {
        "key": "masters",
        "title": "Добавьте мастеров",
        "hint": "Кто принимает клиентов и что умеет",
        "link": "/masters",
    },
    {
        "key": "master_services",
        "title": "Назначьте мастерам услуги",
        "hint": "Иначе некому будет записать клиента",
        "link": "/masters",
    },
    {
        "key": "working_hours",
        "title": "Задайте график работы",
        "hint": "Из графика получаются слоты для записи",
        "link": "/masters",
    },
    {
        "key": "bookings",
        "title": "Примите первую запись",
        "hint": "Через бота или вручную — как удобно",
        "link": "/bookings",
    },
)

# SQL-подсчёт для каждого шага. Всё изолировано по salon_id (мультиарендность).
_COUNT_SQL: dict[str, str] = {
    "services": "SELECT count(*) FROM services WHERE salon_id = $1",
    "masters": "SELECT count(*) FROM masters WHERE salon_id = $1",
    "master_services": (
        "SELECT count(*) FROM master_services ms JOIN masters m ON m.id = ms.master_id "
        "WHERE m.salon_id = $1"
    ),
    "working_hours": (
        "SELECT count(*) FROM working_hours wh JOIN masters m ON m.id = wh.master_id "
        "WHERE m.salon_id = $1"
    ),
    "bookings": "SELECT count(*) FROM bookings WHERE salon_id = $1",
}


async def onboarding_steps(pool: Any, salon: dict[str, Any]) -> dict[str, Any]:
    """Прогресс онбординга: по шагу — выполнено ли он и сколько уже есть.

    Возвращает ``steps`` (список с done/count) и ``done``/``total`` — чтобы
    панель знала, показывать ли карточку вообще (её прячем при 100%).
    """
    sid = str(salon["id"])
    steps: list[dict[str, Any]] = []
    for spec in STEPS:
        count = int(await pool.fetchval(_COUNT_SQL[spec["key"]], sid) or 0)
        steps.append({**spec, "done": count > 0, "count": count})
    done = sum(1 for s in steps if s["done"])
    return {"steps": steps, "done": done, "total": len(steps)}


def onboarding_card(onboarding: dict[str, Any] | None, sid: str = "") -> str:
    """Карточка «Первые шаги» с прогрессом; пустая строка, если всё готово.

    Выполненные шаги — зелёная галочка, невыполненные — ссылка с подсказкой.
    Прогресс-полоска переиспользует стиль индикаторов лимита (.usage-track).
    """
    if not onboarding or not onboarding.get("steps"):
        return ""
    steps = onboarding["steps"]
    done = int(onboarding.get("done", 0))
    total = int(onboarding.get("total", len(steps)))
    if total == 0 or done >= total:
        return ""  # онбординг пройден — не занимаем место на главной
    percent = min(100, round(100 * done / total))
    q = f"?salon={esc(sid)}" if sid else ""
    items: list[str] = []
    for step in steps:
        if step["done"]:
            items.append(
                '<li class="onb done"><span class="onb-mark">✓</span>'
                f'<span class="onb-text">{esc(step["title"])}</span></li>'
            )
        else:
            link = f'{esc(step["link"])}{q}'
            items.append(
                '<li class="onb"><span class="onb-mark">○</span>'
                f'<a class="onb-link" href="{link}">{esc(step["title"])}</a>'
                f'<span class="onb-hint">{esc(step["hint"])}</span></li>'
            )
    body = (
        f'<div class="usage"><div class="usage-track">'
        f'<div class="usage-fill u-accent" style="width:{percent}%"></div></div>'
        f'<span class="usage-text">{done} из {total}</span></div>'
        f'<ul class="onb-list">{"".join(items)}</ul>'
    )
    return card(f"Первые шаги · осталось {total - done}", body)
