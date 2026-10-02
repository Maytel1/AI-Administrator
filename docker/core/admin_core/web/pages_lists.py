"""Страницы панели: клиенты, прайс, мастера, диалоги, аудит, настройки."""

from __future__ import annotations

import json
from typing import Any
from zoneinfo import ZoneInfo

from .render import (
    card,
    csrf_field,
    dt_local,
    esc,
    layout,
    money,
    table,
)
from .render import (
    flash as flash_html,
)


def page_clients(
    rows: list[dict[str, Any]],
    tz: ZoneInfo,
    salon: dict | None,
    salons: list[dict] | None,
    csrf: str = "",
    flash: str = "",
    flash_error: bool = False,
) -> str:
    """Клиенты точки: предоплата и заметки правятся прямо здесь — бот читает их в диалоге."""
    sid = esc(salon["id"]) if salon else ""
    back = f"/clients?salon={sid}"
    cards = []
    for c in rows:
        prepay_checked = ' checked' if c["requires_prepayment"] else ""
        form = (
            '<form method="post" action="/actions" class="stack">'
            '<input type="hidden" name="action" value="client_save">'
            f'<input type="hidden" name="back" value="{back}">'
            f'<input type="hidden" name="client_id" value="{esc(c["id"])}">'
            f"{csrf_field(csrf)}"
            '<div class="cell"><span>Предоплата</span>'
            f'<label class="check"><input type="checkbox" name="requires_prepayment"{prepay_checked}>'
            "требует</label></div>"
            '<div class="cell"><span>Заметки (аллергии, пожелания)</span>'
            f'<input type="text" name="notes" value="{esc(c["notes"] or "")}" '
            'placeholder="например: аллергия на латекс"></div>'
            '<div class="cell"><span>&nbsp;</span>'
            "<button type=\"submit\">Сохранить</button></div>"
            "</form>"
        )
        cards.append(
            f'<div class="item"><b>{esc(c["name"] or "Без имени")}</b> '
            f'<span class="meta">{esc(c["phone"])} · визитов: {c["completed_visits"]} · '
            f'в очереди: {c["upcoming"]} · был(а): '
            f'{esc(dt_local(c["last_visit"], tz, "%d.%m.%Y"))}</span>{form}</div>'
        )
    body = "".join(cards) or (
        '<div class="empty">Клиентов пока нет — они появятся после первых заявок.</div>'
    )
    content = (
        f"<h1>Клиенты</h1>"
        f'<p class="sub">Карточек: {len(rows)}. Опознание «своих» — по номеру телефона</p>'
        + flash_html(flash, flash_error)
        + card("Карточки клиентов", body)
    )
    return layout("Клиенты", "/clients", content, salon=salon, salons=salons)


def page_services(
    rows: list[dict[str, Any]],
    salon: dict | None,
    salons: list[dict] | None,
    csrf: str = "",
    flash: str = "",
    flash_error: bool = False,
) -> str:
    """Прайс с правкой цены/длительности/активности — бот читает эти данные в диалоге."""
    sid = esc(salon["id"]) if salon else ""
    back = f"/services?salon={sid}"
    cards = []
    for s in rows:
        active_checked = " checked" if s["is_active"] else ""
        fields = (
            f'<input type="hidden" name="service_id" value="{esc(s["id"])}">'
            '<div class="cell"><span>Цена, ₽</span>'
            f'<input type="number" name="price" value="{int(s["price"] or 0)}" step="50" min="0">'
            "</div>"
            '<div class="cell"><span>Минут</span>'
            f'<input type="number" name="duration_min" value="{s["duration_min"]}" '
            'step="5" min="5" max="600"></div>'
            '<div class="cell"><span>Активна</span>'
            f'<label class="check"><input type="checkbox" name="is_active"{active_checked}>да</label>'
            "</div>"
        )
        form = (
            '<form method="post" action="/actions" class="stack">'
            '<input type="hidden" name="action" value="service_save">'
            f'<input type="hidden" name="back" value="{back}">'
            f"{csrf_field(csrf)}{fields}"
            '<div class="cell"><span>&nbsp;</span><button type="submit">Сохранить</button></div>'
            "</form>"
        )
        cards.append(
            f'<div class="item"><b>{esc(s["title"])}</b>'
            f'<span class="meta">{s["duration_min"]} мин · {money(s["price"])} · '
            f'{"активна" if s["is_active"] else "выключена"}</span>{form}</div>'
        )
    body = "".join(cards) or (
        '<div class="empty">Прайс не загружен: docker compose run --rm cli python cli.py --seed</div>'
    )
    revenue = sum(float(s["price"] or 0) for s in rows)
    content = (
        "<h1>Прайс</h1>"
        f'<p class="sub">Услуг: {len(rows)}, сумма позиций: {money(revenue)}. '
        "Изменения сразу слышит бот в разговоре с клиентом</p>"
        + flash_html(flash, flash_error)
        + card("Услуги", f'<div class="grid-cards">{body}</div>' if cards else body)
    )
    return layout("Прайс", "/services", content, salon=salon, salons=salons)


def page_masters(
    rows: list[dict[str, Any]],
    salon: dict | None,
    salons: list[dict] | None,
    csrf: str = "",
    flash: str = "",
    flash_error: bool = False,
) -> str:
    """Мастера и их график: слоты нарезаются из него — правка окна меняет доступное время."""
    from .queries import WEEKDAY_RU

    sid = esc(salon["id"]) if salon else ""
    back = f"/masters?salon={sid}"
    cards = []
    for m in rows:
        hours_raw = m["hours"]
        if isinstance(hours_raw, str):
            hours_raw = json.loads(hours_raw or "[]")
        by_day = {int(h["weekday"]): h for h in hours_raw}
        days = []
        for wd in range(7):
            h = by_day.get(wd)
            enabled = h is not None
            start = str((h or {}).get("start", "10:00:00"))[:5]
            end = str((h or {}).get("end", "20:00:00"))[:5]
            checked = " checked" if enabled else ""
            fields = (
                f'<input type="hidden" name="master_id" value="{esc(m["id"])}">'
                f'<input type="hidden" name="weekday" value="{wd}">'
                f'<div class="cell"><span>{WEEKDAY_RU[wd]}</span>'
                f'<label class="check"><input type="checkbox" name="enabled"{checked}>работает</label>'
                "</div>"
                '<div class="cell"><span>с</span>'
                f'<input type="time" name="start" value="{start}"></div>'
                '<div class="cell"><span>по</span>'
                f'<input type="time" name="end" value="{end}"></div>'
                '<div class="cell"><span>&nbsp;</span><button type="submit">Ок</button></div>'
            )
            days.append(
                '<form method="post" action="/actions" class="stack">'
                '<input type="hidden" name="action" value="hours_save">'
                f'<input type="hidden" name="back" value="{back}">'
                f"{csrf_field(csrf)}{fields}</form>"
            )
        summary = ", ".join(
            f"{WEEKDAY_RU[int(h['weekday'])]} {str(h['start'])[:5]}–{str(h['end'])[:5]}"
            for h in sorted(hours_raw, key=lambda x: int(x["weekday"]))
        )
        cards.append(
            f'<div class="item"><b>{esc(m["name"])}</b>'
            f'<span class="meta">умений: {m["services_count"]} · {esc(summary or "график не задан")}</span>'
            f'<div style="display:grid;gap:8px">{"".join(days)}</div></div>'
        )
    body = "".join(cards) or '<div class="empty">Мастера не заведены.</div>'
    content = (
        "<h1>Мастера</h1>"
        '<p class="sub">График — источник слотов: сузили окно, время исчезло у клиентов</p>'
        + flash_html(flash, flash_error)
        + card("Команда и график", body)
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
