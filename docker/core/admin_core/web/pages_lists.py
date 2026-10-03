"""Страницы панели: клиенты, прайс, мастера, диалоги, аудит, настройки."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .pages_core import _ctx_parts
from .render import (
    action_form,
    badge,
    card,
    csrf_field,
    dt_local,
    esc,
    layout,
    money,
    table,
    tile,
)
from .render import (
    flash as flash_html,
)


def _role(ctx: Any | None) -> str:
    """Роль посетителя для каркаса: что показывать в меню.

    Берётся из контекста запроса, а не из формы: иначе ссылку в меню можно
    было бы выдумать себе ролью в параметрах.
    """
    return str(getattr(ctx, "role", "") or "")


def waitlist_block(
    queue: list[dict[str, Any]],
    services: list[dict[str, Any]],
    ctx: Any | None,
    salon: dict[str, Any] | None,
) -> str:
    """Карточка «Лист ожидания»: кто ждёт и форма добавления.

    Пустая очередь — это не ошибка, а «никто не ждёт»: блок нужен всегда,
    иначе владелец не знает, что возможность есть.
    """
    csrf, _may_drop = _ctx_parts(ctx, "client_create")
    sid = esc(salon["id"]) if salon else ""
    back = f"/clients?salon={sid}"
    rows = [
        [
            esc(item.get("phone") or "—"),
            esc(item.get("name") or "без имени"),
            esc(item.get("service_title") or "любая услуга"),
            esc((item.get("note") or "")[:40]),
            dt_local(item.get("created_at"), ZoneInfo("Asia/Yekaterinburg")),
            action_form(
                "waitlist_drop", csrf, back,
                f'<input type="hidden" name="waitlist_id" value="{esc(item["id"])}">',
                "Убрать",
            ),
        ]
        for item in queue
    ]
    options = "".join(
        f'<option value="{esc(s["id"])}">{esc(s["title"])}</option>'
        for s in services
    )
    form = (
        '<form method="post" action="/actions" class="stack" '
        'style="margin-top:12px">'
        '<input type="hidden" name="action" value="waitlist_add">'
        f'<input type="hidden" name="back" value="{back}">'
        f"{csrf_field(csrf)}"
        '<div class="cell"><span>Телефон</span>'
        '<input type="tel" name="phone" placeholder="+79001234567" required></div>'
        '<div class="cell"><span>Услуга</span>'
        f'<select name="service_id"><option value="">любая</option>{options}</select></div>'
        '<div class="cell"><span>Заметка</span>'
        '<input type="text" name="note" placeholder="хочет утром"></div>'
        '<div class="cell"><span>&nbsp;</span>'
        '<button type="submit">В лист ожидания</button></div>'
        "</form>"
    )
    body = table(
        ["Телефон", "Клиент", "Услуга", "Заметка", "С", "Действие"], rows,
        "Пока никто не ждёт — сюда можно поставить клиента",
    )
    return card(f"Лист ожидания ({len(queue)})", body + form)


def page_network(
    rows: list[dict[str, Any]],
    totals: dict[str, Any],
    salon: dict | None,
    salons: list[dict] | None,
    flash: str = "",
    flash_error: bool = False,
    days: int = 30,
    ctx: Any | None = None,
) -> str:
    """Сводка по всем точкам — то, ради чего нужна роль «наша команда».

    Владелец точки сюда не попадает: функция вызывается только при ``all_salons``,
    а лишняя навигация у него была бы враньём в интерфейсе.
    """
    table_rows = [
        [
            esc(row["salon"]),
            badge(str(row.get("plan") or "—"), "accent"),
            str(row["bookings"]),
            money(row["revenue"]),
            str(row["no_shows"]),
            str(row["clients"]),
        ]
        for row in rows
    ]
    tiles = "".join(
        [
            tile(str(totals.get("salons", 0)), "точек в сети"),
            tile(money(totals.get("revenue", 0.0)), f"выручка за {days} дней", "green"),
            tile(str(totals.get("bookings", 0)), "записей"),
            tile(str(totals.get("no_shows", 0)), "неявок", "yellow"),
            tile(str(totals.get("clients", 0)), "клиентов всего"),
        ]
    )
    content = (
        "<h1>Сеть точек</h1>"
        f'<p class="sub">Все точки, где мы работаем · период {days} дней</p>'
        + flash_html(flash, flash_error)
        + f'<div class="tiles">{tiles}</div>'
        + card(
            "Точки",
            table(
                ["Точка", "Тариф", "Записей", "Выручка", "Неявок", "Клиентов"],
                table_rows, "Активных точек нет",
            ),
        )
    )
    return layout("Сеть", "/network", content, salon=salon, salons=salons, role=_role(ctx))


def page_clients(
    rows: list[dict[str, Any]],
    tz: ZoneInfo,
    salon: dict | None,
    salons: list[dict] | None,
    ctx: Any | None = None,
    flash: str = "",
    flash_error: bool = False,
) -> str:
    """Клиенты точки: предоплата и заметки правятся прямо здесь — бот читает их в диалоге."""
    csrf, may_edit = _ctx_parts(ctx, "client_flags")
    _, may_add = _ctx_parts(ctx, "client_create")
    sid = esc(salon["id"]) if salon else ""
    back = f"/clients?salon={sid}"
    cards = []
    for c in rows:
        head = (
            f'<div class="item"><b>{esc(c["name"] or "Без имени")}</b> '
            f'<span class="meta">{esc(c["phone"])} · визитов: {c["completed_visits"]} · '
            f'в очереди: {c["upcoming"]} · был(а): '
            f'{esc(dt_local(c["last_visit"], tz, "%d.%m.%Y"))}</span>'
        )
        if not may_edit:
            cards.append(head + "</div>")
            continue
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
        cards.append(head + form + "</div>")
    body = "".join(cards) or (
        '<div class="empty">Клиентов пока нет — они появятся после первых заявок.</div>'
    )
    add_form = ""
    if may_add:
        add_form = (
            '<form method="post" action="/actions" class="stack" style="margin-top:14px">'
            '<input type="hidden" name="action" value="client_create">'
            f'<input type="hidden" name="back" value="{back}">'
            f"{csrf_field(csrf)}"
            '<div class="cell"><span>Телефон</span>'
            '<input type="tel" name="phone" placeholder="+79001234567" required></div>'
            '<div class="cell"><span>Имя</span>'
            '<input type="text" name="name" placeholder="Как обращаться"></div>'
            '<div class="cell"><span>Заметки</span>'
            '<input type="text" name="notes" placeholder="аллергии, пожелания"></div>'
            '<div class="cell"><span>&nbsp;</span>'
            '<button type="submit">Добавить</button></div>'
            "</form>"
        )
    content = (
        f"<h1>Клиенты</h1>"
        f'<p class="sub">Карточек: {len(rows)}. Опознание «своих» — по номеру телефона</p>'
        + flash_html(flash, flash_error)
        + card("Карточки клиентов", body + add_form)
        + "<!--WAITLIST-->"
    )
    return layout("Клиенты", "/clients", content, salon=salon, salons=salons, role=_role(ctx))


def page_services(
    rows: list[dict[str, Any]],
    salon: dict | None,
    salons: list[dict] | None,
    ctx: Any | None = None,
    flash: str = "",
    flash_error: bool = False,
) -> str:
    """Прайс с правкой цены/длительности/активности — бот читает эти данные в диалоге."""
    csrf, may_edit = _ctx_parts(ctx, "pricing")
    _, may_add = _ctx_parts(ctx, "catalog")
    sid = esc(salon["id"]) if salon else ""
    back = f"/services?salon={sid}"
    cards = []
    for s in rows:
        head = (
            f'<div class="item"><b>{esc(s["title"])}</b>'
            f'<span class="meta">{s["duration_min"]} мин · {money(s["price"])} · '
            f'{"активна" if s["is_active"] else "в архиве"}</span>'
        )
        if not may_edit:
            cards.append(head + "</div>")
            continue
        active_checked = " checked" if s["is_active"] else ""
        toggle = None
        if may_add:
            toggle = action_form(
                "service_archive", csrf, back,
                f'<input type="hidden" name="service_id" value="{esc(s["id"])}">'
                f'<input type="hidden" name="active" value="{"0" if s["is_active"] else "1"}">',
                "Вернуть в прайс" if not s["is_active"] else "В архив",
            )
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
        cards.append(head + form + (toggle or "") + "</div>")
    body = "".join(cards) or (
        '<div class="empty">Прайс не загружен: docker compose run --rm cli python cli.py --seed</div>'
    )
    add_form = ""
    if may_add:
        add_form = (
            '<form method="post" action="/actions" class="stack" style="margin-top:14px">'
            '<input type="hidden" name="action" value="service_create">'
            f'<input type="hidden" name="back" value="{back}">'
            f"{csrf_field(csrf)}"
            '<div class="cell"><span>Новая услуга</span>'
            '<input type="text" name="title" placeholder="название" required></div>'
            '<div class="cell"><span>Цена, ₽</span>'
            '<input type="number" name="price" min="0" step="50" required></div>'
            '<div class="cell"><span>Минут</span>'
            '<input type="number" name="duration_min" value="60" min="5" max="600" '
            'step="5" required></div>'
            '<div class="cell"><span>&nbsp;</span>'
            '<button type="submit">Добавить</button></div>'
            "</form>"
        )
    revenue = sum(float(s["price"] or 0) for s in rows)
    content = (
        "<h1>Прайс</h1>"
        f'<p class="sub">Услуг: {len(rows)}, сумма позиций: {money(revenue)}. '
        "Изменения сразу слышит бот в разговоре с клиентом</p>"
        + flash_html(flash, flash_error)
        + card(
            "Услуги",
            (f'<div class="grid-cards">{body}</div>' if cards else body) + add_form,
        )
    )
    return layout("Прайс", "/services", content, salon=salon, salons=salons, role=_role(ctx))


def page_masters(
    rows: list[dict[str, Any]],
    salon: dict | None,
    salons: list[dict] | None,
    ctx: Any | None = None,
    flash: str = "",
    flash_error: bool = False,
) -> str:
    """Мастера и их график: слоты нарезаются из него — правка окна меняет доступное время."""
    from .queries import WEEKDAY_RU

    csrf, may_edit = _ctx_parts(ctx, "schedule")
    _, may_add = _ctx_parts(ctx, "catalog")
    sid = esc(salon["id"]) if salon else ""
    back = f"/masters?salon={sid}"
    cards = []
    for m in rows:
        hours_raw = m["hours"]
        if isinstance(hours_raw, str):
            hours_raw = json.loads(hours_raw or "[]")
        by_day = {int(h["weekday"]): h for h in hours_raw}
        days = []
        for wd in range(7) if may_edit else ():
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
    add_form = ""
    if may_add:
        add_form = (
            '<form method="post" action="/actions" class="stack" style="margin-top:14px">'
            '<input type="hidden" name="action" value="master_create">'
            f'<input type="hidden" name="back" value="{back}">'
            f"{csrf_field(csrf)}"
            '<div class="cell"><span>Новый мастер</span>'
            '<input type="text" name="name" placeholder="Имя" required></div>'
            '<div class="cell"><span>&nbsp;</span>'
            '<button type="submit">Добавить</button></div>'
            "</form>"
        )
    content = (
        "<h1>Мастера</h1>"
        '<p class="sub">График — источник слотов: сузили окно, время исчезло у клиентов</p>'
        + flash_html(flash, flash_error)
        + card("Команда и график", body + add_form)
    )
    return layout("Мастера", "/masters", content, salon=salon, salons=salons, role=_role(ctx))
def page_dialogs(
    rows: list[dict[str, Any]],
    messages: list[dict[str, Any]] | None,
    tz: ZoneInfo,
    salon: dict | None,
    salons: list[dict] | None,
    ctx: Any | None = None,
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
    return layout("Диалоги", "/dialogs", content, salon=salon, salons=salons, role=_role(ctx))


def page_audit(
    rows: list[dict[str, Any]],
    tz: ZoneInfo,
    salon: dict | None,
    salons: list[dict] | None,
    ctx: Any | None = None,
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
    return layout("Аудит", "/audit", content, salon=salon, salons=salons, role=_role(ctx))


def page_settings(
    salon: dict | None,
    salons: list[dict] | None,
    env: dict[str, str],
    session: dict[str, Any] | None = None,
    team: list[dict[str, Any]] | None = None,
    ctx: Any | None = None,
    once: list[dict[str, Any]] | None = None,
) -> str:
    """Настройки точки: тариф, CRM, кто вошёл, команда и одноразовые секреты."""
    from .pages_core import _ctx_parts
    from .queries import PLAN_RU
    from .roles import ROLE_RU, SELF_ISSUE_ROLES, permissions_of, role_label

    info = salon or {}
    me = session or {}
    role = str(me.get("role", "guest"))
    may_users = bool(team is not None)
    csrf, may_edit = _ctx_parts(ctx, "users")
    sid = esc(salon["id"]) if salon else ""
    back = f"/settings?salon={sid}"
    body = [
        ["Кто вошёл", f"{me.get('login', '—')} · {role_label(role)}"],
        ["Права роли", ", ".join(permissions_of(role)) or "только просмотр"],
        ["Название", esc(info.get("name", "—"))],
        ["Тариф", esc(PLAN_RU.get(str(info.get("plan")), str(info.get("plan", "—"))))],
        ["CRM", esc(info.get("crm_provider", "none"))],
        ["Таймзона", esc(info.get("timezone", "—"))],
        ["Режим бота", "Telegram long polling"],
        ["LLM", esc(env.get("LLM_PROVIDER", "—"))],
        ["Версия панели", "MVP 0.3 (HTTPS, роли, 2FA, команда)"],
    ]

    secrets_block = ""
    if once:
        parts = []
        for item in once:
            fields = "".join(
                f'<div class="meta">{esc(k)}: <b>{esc(str(v))}</b></div>'
                for k, v in item.items()
                if k != "note" and v
            )
            parts.append(
                f'<div class="item">{fields}'
                f'<div class="meta">{esc(item.get("note", ""))}</div></div>'
            )
        secrets_block = card(
            "Показываем один раз — запишите или передайте сотруднику сейчас",
            "".join(parts),
        )

    people = ""
    if may_users:
        rows = []
        for u in team or []:
            if not may_edit:
                rows.append(
                    [esc(u["login"]), esc(u["name"] or "—"), role_label(str(u["role"])),
                     badge("2FA включена", "green") if u["totp_enabled"]
                     else badge("без 2FA", "yellow"),
                     esc(dt_local(u["last_login_at"], ZoneInfo("UTC"), "%d.%m.%Y %H:%M"))]
                )
                continue
            hidden = f'<input type="hidden" name="login" value="{esc(u["login"])}">'
            hidden_id = f'<input type="hidden" name="user_id" value="{esc(u["id"])}">'
            buttons = action_form(
                "user_totp", csrf, back, hidden, "Включить 2FA"
            ) + " " + action_form("user_disable", csrf, back, hidden_id, "Отключить")
            rows.append(
                [esc(u["login"]), esc(u["name"] or "—"), role_label(str(u["role"])),
                 badge("2FA включена", "green") if u["totp_enabled"]
                 else badge("без 2FA", "yellow"),
                 esc(dt_local(u["last_login_at"], ZoneInfo("UTC"), "%d.%m.%Y %H:%M")),
                 buttons]
            )
        options = "".join(
            f'<option value="{esc(r)}">{esc(ROLE_RU[r])}</option>' for r in SELF_ISSUE_ROLES
        )
        add_form = ""
        if may_edit:
            add_form = (
                '<form method="post" action="/actions" class="stack" '
                'style="margin-top:12px">'
                '<input type="hidden" name="action" value="user_create">'
                f'<input type="hidden" name="back" value="{back}">'
                f"{csrf_field(csrf)}"
                '<div class="cell"><span>Логин (e-mail)</span>'
                '<input type="email" name="login" placeholder="recept@salon.ru" required></div>'
                '<div class="cell"><span>Имя</span>'
                '<input type="text" name="name" placeholder="Как обращаться"></div>'
                f'<div class="cell"><span>Роль</span><select name="role">{options}</select></div>'
                '<div class="cell"><span>&nbsp;</span>'
                "<button type=\"submit\">Создать</button></div>"
                "</form>"
            )
        people = card(
            "Команда точки",
            table(
                ["Логин", "Имя", "Роль", "Второй фактор", "Был(а)", "Действия"],
                rows, "Пока никого",
            ) + add_form,
        )

    note = (
        '<div class="card"><h2>Что дальше</h2><p class="sub" style="margin:0">'
        "Смена своего пароля, отчёты и биллинг — по плану фазы 4 "
        "(DEVELOPMENT_PLAN.md §8.2)."
        "</p></div>"
    )
    content = (
        "<h1>Настройки</h1>"
        '<p class="sub">Параметры точки и доступ в панель</p>'
        + secrets_block
        + card("Точка и доступ", table(["Параметр", "Значение"], body))
        + people
        + note
    )
    return layout("Настройки", "/settings", content, salon=salon, salons=salons, role=_role(ctx))
def _bar_chart(rows: list[dict[str, Any]], tz: ZoneInfo) -> str:
    """Столбики выручки по дням — без JS и внешних библиотек.

    Высота считается от максимума периода, поэтому дни сравнимы между собой.
    """
    if not rows:
        return '<div class="empty">За период записей не было</div>'
    peak = max(float(r["revenue"] or 0) for r in rows) or 1.0
    bars = []
    for row in rows:
        height = max(2, int(100 * float(row["revenue"] or 0) / peak))
        day = row["day"]
        label = day.strftime("%d.%m") if isinstance(day, datetime) else str(day)[-5:]
        bars.append(
            f'<div class="bar-col" title="{esc(label)}: {esc(money(row["revenue"]))}">'
            f'<div class="bar-fill" style="height:{height}%"></div>'
            f'<span>{esc(label)}</span></div>'
        )
    return f'<div class="chart">{"".join(bars)}</div>'


def page_reports(
    data: dict[str, Any],
    salon: dict | None,
    salons: list[dict] | None,
    flash: str = "",
    flash_error: bool = False,
    ctx: Any | None = None,
) -> str:
    """Отчёт за выбранный период + сравнение с предыдущим таким же."""
    stats = data["funnel"]
    diff = data.get("diff") or {}
    window = data.get("window")
    label = window.label if window else "период"
    prev_window = data.get("previous_window")
    prev_label = prev_window.label if prev_window else "прошлый период"
    tiles = "".join(
        [
            tile(money(stats["revenue"]), f"выручка · {label}", "green",
                 _trend(diff.get("revenue"))),
            tile(str(stats["bookings"]), "записей", "", _trend(diff.get("bookings"))),
            tile(str(stats["dialogs"]), "диалогов с клиентами", "",
                 _trend(diff.get("dialogs"))),
            tile(f'{stats["conversion"]}%', "конверсия диалогов в запись", "accent",
                 _trend(diff.get("conversion"))),
            tile(money(stats["avg_check"]), "средний чек", "",
                 _trend(diff.get("avg_check"))),
            tile(f'{data["loss"]["lost_rate"]}%', "отмен и неявок", "yellow",
                 _trend(diff.get("lost_rate"), good_down=True)),
        ]
    )
    load_rows = [
        [
            esc(m["name"]),
            str(m["bookings"]),
            f'{float(m["busy_hours"]):.1f} ч',
            money(m["revenue"]),
        ]
        for m in data["load"]
    ]
    loss_rows = [
        ["Всего записей", str(data["loss"]["total"])],
        ["Отменено клиентом", str(data["loss"]["cancelled"])],
        ["Не пришёл", str(data["loss"]["no_show"])],
        ["Новых клиентов", str(stats["new_clients"])],
        ["Затраты на LLM", money(stats["llm_cost"])],
    ]
    content = (
        "<h1>Отчёты</h1>"
        + flash_html(flash, flash_error)
        + _period_tabs(data.get("periods") or [], data.get("period_key") or "30", salon)
        + '<div class="row-end">'
        + '<a class="tab" href="/export.csv?kind=daily&amp;period='
        + f'{esc(data.get("period_key") or "30")}">CSV по дням</a> '
        + '<a class="tab" href="/export.csv?kind=masters&amp;period='
        + f'{esc(data.get("period_key") or "30")}">CSV по мастерам</a>'
        + "</div>"
        + f'<p class="sub">{esc(label)} · сравнение с {esc(prev_label)}'
        + " · по факту записей и диалогов</p>"
        + f'<div class="tiles">{tiles}</div>'
        + card("Выручка по дням", _bar_chart(data["by_day"], ZoneInfo("UTC")))
        + card(
            "Сравнение с прошлым периодом",
            _compare_table(diff, prev_label) if diff else
            '<div class="empty">Сравнение появится вместе с выбором периода</div>',
        )
        + card("Загрузка мастеров", table(
            ["Мастер", "Записей", "Занято", "Выручка"], load_rows, "Данных пока нет",
        ))
        + card("Потери и затраты", table(["Показатель", "Значение"], loss_rows))
    )
    return layout("Отчёты", "/reports", content, salon=salon, salons=salons, role=_role(ctx))


def _period_tabs(periods: list[tuple[str, str, Any]], current: str, salon: dict | None) -> str:
    """Переключатель периода: ссылки, а не форма — период видно в адресной строке."""
    if not periods:
        return ""
    sid = esc(salon["id"]) if salon else ""
    items = []
    for key, label, _days in periods:
        active = " tab-active" if key == current else ""
        link = f"/reports?period={esc(key)}" + (f"&amp;salon={sid}" if sid else "")
        items.append(f'<a class="tab{active}" href="{link}">{esc(label)}</a>')
    return f'<div class="tabs">{"".join(items)}</div>'


def _trend(value: dict[str, Any] | None, *, good_down: bool = False) -> str:
    """Подпись «+12 % к прошлому периоду» под плиткой.

    Пустой прочерк, когда сравнивать не с чем: «+100 %» от нуля — не результат,
    а ошибка деления, и показывать её нельзя.
    """
    if not value:
        return ""
    percent = value["percent"]
    if percent is None:
        return '<span class="trend muted">не с чем сравнить</span>'
    tone = "up" if value["change"] > 0 else ("down" if value["change"] < 0 else "flat")
    if tone != "flat" and good_down:
        tone = "up" if tone == "down" else "down"
    sign = "+" if value["change"] > 0 else ""
    return f'<span class="trend {tone}">{sign}{percent} % к прошлому</span>'


def _compare_table(diff: dict[str, Any], prev_label: str) -> str:
    """Таблица «сейчас / было / изменение» — цифры, а не только проценты."""
    rows: list[tuple[str, str, bool]] = [
        ("Выручка", "revenue", True),
        ("Записей", "bookings", False),
        ("Диалогов", "dialogs", False),
        ("Конверсия, %", "conversion", False),
        ("Средний чек", "avg_check", True),
        ("Отмен и неявок, %", "lost_rate", False),
        ("Новых клиентов", "new_clients", False),
    ]
    out = []
    for title, key, is_money in rows:
        value = diff.get(key)
        if not value:
            continue
        percent = value.get("percent")
        change = f"{percent:+} %" if percent is not None else "—"
        fmt = money if is_money else (lambda x: str(int(round(float(x)))))
        out.append(
            [
                esc(title),
                esc(fmt(value["current"])),
                esc(fmt(value["previous"])),
                esc(change),
            ]
        )
    return table(
        ["Показатель", "Сейчас", esc(prev_label).title(), "Изменение"], out,
        "Пока нет данных за прошлый период",
    )
