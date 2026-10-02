"""Страницы панели: клиенты, прайс, мастера, диалоги, аудит, настройки."""

from __future__ import annotations

import json
from typing import Any
from zoneinfo import ZoneInfo

from .render import badge, card, dt_local, esc, layout, money, table


def page_clients(
    rows: list[dict[str, Any]], tz: ZoneInfo, salon: dict | None, salons: list[dict] | None
) -> str:
    """Клиенты точки: контакты, визиты, флаг предоплаты — то, о чём спрашивает бот."""
    body = [
        [
            esc(c["name"] or "Без имени"),
            esc(c["phone"]),
            str(c["completed_visits"]),
            dt_local(c["last_visit"], tz, "%d.%m.%Y"),
            str(c["upcoming"]),
            badge("требует предоплату", "yellow") if c["requires_prepayment"] else "",
            esc(c["notes"] or ""),
        ]
        for c in rows
    ]
    content = (
        f"<h1>Клиенты</h1>"
        f'<p class="sub">Карточек: {len(rows)}. Опознание «своих» — по номеру телефона</p>'
        + card(
            "Карточки клиентов",
            table(
                ["Имя", "Телефон", "Визитов", "Был(а)", "В очереди", "Предоплата", "Заметки"],
                body,
                "Клиентов пока нет — они появятся после первых заявок.",
            ),
        )
    )
    return layout("Клиенты", "/clients", content, salon=salon, salons=salons)


def page_services(
    rows: list[dict[str, Any]], salon: dict | None, salons: list[dict] | None
) -> str:
    """Прайс, который бот озвучивает клиенту (источник цен — CRM, не чат)."""
    body = [
        [
            esc(s["title"]),
            f'{s["duration_min"]} мин',
            money(s["price"]),
            badge("активна", "green") if s["is_active"] else badge("выключена", "gray"),
        ]
        for s in rows
    ]
    revenue = sum(float(s["price"] or 0) for s in rows)
    content = (
        f"<h1>Прайс</h1>"
        f'<p class="sub">Услуг: {len(rows)}, сумма позиций: {money(revenue)}</p>'
        + card(
            "Услуги",
            table(
                ["Услуга", "Длительность", "Цена", "Статус"],
                body,
                "Прайс не загружен: docker compose run --rm cli python cli.py --seed",
            ),
        )
    )
    return layout("Прайс", "/services", content, salon=salon, salons=salons)


def page_masters(
    rows: list[dict[str, Any]], salon: dict | None, salons: list[dict] | None
) -> str:
    """Мастера и их график: слоты нарезаются из него, поэтому он важен администратору."""
    from .queries import WEEKDAY_RU

    body = []
    for m in rows:
        hours_raw = m["hours"]
        if isinstance(hours_raw, str):
            hours_raw = json.loads(hours_raw or "[]")
        days = []
        for h in hours_raw:
            start, end = str(h["start"] or "")[:5], str(h["end"] or "")[:5]
            days.append(f"{WEEKDAY_RU[int(h['weekday'])]} {start}–{end}")
        body.append(
            [
                esc(m["name"]),
                badge("работает", "green") if m["is_active"] else badge("не работает", "gray"),
                str(m["services_count"]),
                "<br>".join(days) or "—",
            ]
        )
    content = (
        "<h1>Мастера</h1>"
        '<p class="sub">Из графика нарезаются слоты для записей — он влияет на всё расписание</p>'
        + card(
            "Команда и график",
            table(["Мастер", "Статус", "Умений", "Рабочие дни"], body, "Мастера не заведены."),
        )
    )
    return layout("Мастера", "/masters", content, salon=salon, salons=salons)
def page_dialogs(
    rows: list[dict[str, Any]],
    messages: list[dict[str, Any]] | None,
    tz: ZoneInfo,
    salon: dict | None,
    salons: list[dict] | None,
) -> str:
    """Диалоги бота — видно, как бот отвечает клиенту (контроль качества ответов)."""
    from .queries import CHANNEL_RU

    sid = salon["id"] if salon else ""
    body = [
        [
            dt_local(d["started_at"], tz, "%d.%m %H:%M"),
            esc(d["client_name"] or d["client_phone"] or "аноним"),
            esc(CHANNEL_RU.get(d["channel"], d["channel"] or "—")),
            str(d["messages"]),
            esc(d["outcome"] or "—"),
            f'<a class="btn" href="/dialogs?salon={esc(sid)}&id={esc(d["id"])}">Открыть</a>',
        ]
        for d in rows
    ]
    content = (
        "<h1>Диалоги</h1>"
        '<p class="sub">Что бот отвечает клиентам — источник для контроля качества</p>'
        + card(
            "Лента диалогов",
            table(
                ["Когда", "Клиент", "Канал", "Реплик", "Итог", ""],
                body,
                "Диалогов пока нет — бот ещё не общался с клиентами.",
            ),
        )
    )
    if messages is not None:
        bubbles = []
        for m in messages:
            who = {"user": "Клиент", "assistant": "Анна (бот)", "system": "Система",
                   "tool": "Инструмент"}.get(m["role"], m["role"])
            bubbles.append(
                f'<div class="msgs"><b>{esc(who)}</b> · '
                f'{esc(dt_local(m["created_at"], tz, "%d.%m %H:%M"))}'
                f"<br>{esc(m['content'])}</div>"
            )
        content += card("Переписка", "".join(bubbles) or '<div class="empty">Сообщений нет</div>')
    return layout("Диалоги", "/dialogs", content, salon=salon, salons=salons)


def page_audit(
    rows: list[dict[str, Any]], tz: ZoneInfo, salon: dict | None, salons: list[dict] | None
) -> str:
    """Журнал аудита: кто и что сделал — основа для разбора споров."""
    body = []
    for a in rows:
        details = a["details"]
        if isinstance(details, str):
            details = json.loads(details or "{}")
        body.append(
            [
                dt_local(a["ts"], tz, "%d.%m.%Y %H:%M"),
                esc(a["actor"]),
                esc(a["action"]),
                esc(json.dumps(details, ensure_ascii=False)[:160]),
            ]
        )
    content = (
        "<h1>Аудит</h1>"
        '<p class="sub">Каждое значимое действие пишется сюда — бот, человек или система</p>'
        + card(
            "Журнал действий",
            table(["Когда", "Кто", "Действие", "Детали"], body, "Журнал пуст."),
        )
    )
    return layout("Аудит", "/audit", content, salon=salon, salons=salons)


def page_settings(
    salon: dict | None, salons: list[dict] | None, env: dict[str, str]
) -> str:
    """Настройки точки: тариф, CRM, таймзона — плюс состояние контура."""
    from .queries import PLAN_RU

    info = salon or {}
    body = [
        ["Название", esc(info.get("name", "—"))],
        ["Тариф", esc(PLAN_RU.get(str(info.get("plan")), str(info.get("plan", "—"))))],
        ["CRM", esc(info.get("crm_provider", "none"))],
        ["Таймзона", esc(info.get("timezone", "—"))],
        ["Режим бота", "Telegram long polling"],
        ["LLM", esc(env.get("LLM_PROVIDER", "—"))],
        ["Версия панели", "MVP 0.1 (read-only)"],
    ]
    note = (
        '<div class="card"><h2>Что дальше</h2><p class="sub" style="margin:0">'
        "Редактирование расписания, услуг и FAQ, сотрудники и роли, биллинг — "
        "по плану фазы 4 (DEVELOPMENT_PLAN.md §8.2). Сейчас панель только читает данные."
        "</p></div>"
    )
    content = (
        "<h1>Настройки</h1>"
        '<p class="sub">Параметры подключённой точки</p>'
        + card("Точка", table(["Параметр", "Значение"], body))
        + note
    )
    return layout("Настройки", "/settings", content, salon=salon, salons=salons)
