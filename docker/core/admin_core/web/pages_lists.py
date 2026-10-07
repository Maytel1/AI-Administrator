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
    page_head,
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
        page_head(
            "Сеть точек",
            f"Все точки, где мы работаем · период {days} дней",
            salon, salons,
        )
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


def _client_window(
    c: dict[str, Any],
    cid: str,
    csrf: str,
    back: str,
    tz: ZoneInfo,
) -> str:
    """Окно карточки клиента: визиты — строками, правка — формой под ними.

    Тот же приём, что у мастеров: чекбокс-тумблер и sibling-селектор,
    JS в панели нет. Кнопка — label на тумблере, штора и «✕» закрывают.
    """
    prepay = " checked" if c.get("requires_prepayment") else ""
    stats = (
        f'<span class="meta">Телефон: {esc(c.get("phone") or "")}</span>'
        f'<span class="meta">Визитов: {int(c.get("completed_visits") or 0)} · '
        f'в очереди: {int(c.get("upcoming") or 0)} · был(а): '
        f'{esc(dt_local(c.get("last_visit"), tz, "%d.%m.%Y"))}</span>'
        f'<span class="meta">Клиент с: {esc(dt_local(c.get("created_at"), tz, "%d.%m.%Y"))}</span>'
    )
    return (
        '<div class="modal">'
        f'<label class="modal-veil" for="ck-{cid}"></label>'
        '<form method="post" action="/actions" class="modal-card">'
        '<div class="modal-head">'
        f'<b>Карточка — {esc(c.get("name") or "Без имени")}</b>'
        f'<label class="modal-x" for="ck-{cid}">✕</label></div>'
        f'<div class="modal-body stack">{stats}'
        '<label class="check">'
        f'<input type="checkbox" name="requires_prepayment"{prepay}>требует предоплаты</label>'
        '<label class="field"><span>Заметки (аллергии, пожелания)</span>'
        f'<input type="text" name="notes" value="{esc(c.get("notes") or "")}" '
        'placeholder="например: аллергия на латекс"></label>'
        "</div>"
        '<div class="modal-foot">'
        '<input type="hidden" name="action" value="client_save">'
        f'<input type="hidden" name="client_id" value="{cid}">'
        f'<input type="hidden" name="back" value="{back}">'
        f"{csrf_field(csrf)}"
        f'<label class="btn ghost" for="ck-{cid}">Отмена</label>'
        '<button type="submit">Сохранить</button></div></form></div>'
    )


def page_clients(
    rows: list[dict[str, Any]],
    tz: ZoneInfo,
    salon: dict | None,
    salons: list[dict] | None,
    ctx: Any | None = None,
    flash: str = "",
    flash_error: bool = False,
) -> str:
    """Клиенты плитками: на карточке — кратко, правка — окном поверх.

    Карточка говорит, кто это и сколько раз приходил; предоплата и заметки
    открываются окном на чистом CSS — как у мастеров, JS в панели нет.
    Бот читает эти же данные в диалоге.
    """
    csrf, may_edit = _ctx_parts(ctx, "client_flags")
    _, may_add = _ctx_parts(ctx, "client_create")
    sid = esc(salon["id"]) if salon else ""
    back = f"/clients?salon={sid}"
    cards = []
    for c in rows:
        cid = esc(c["id"])
        name = esc(c["name"] or "Без имени")
        phone = esc(c["phone"])
        visits = int(c["completed_visits"] or 0)
        queued = int(c["upcoming"] or 0)
        prepay_badge = badge("предоплата", "yellow") if c["requires_prepayment"] else ""
        # ── кратко на плитке: кто, когда был, ждёт ли записи ────────────
        buttons, windows = "", ""
        if may_edit:
            buttons = f'<label class="btn" for="ck-{cid}">Карточка</label>'
            windows = f'<input type="checkbox" id="ck-{cid}" class="modal-toggle">'
            windows += _client_window(c, cid, csrf, back, tz)
        actions = f'<div class="m-actions">{buttons}</div>' if buttons else ""
        cards.append(
            '<div class="m-card">'
            f'<div class="m-top"><b>{name}</b>{prepay_badge}</div>'
            f'<span class="meta">{phone} · визитов: {visits} · в очереди: {queued}</span>'
            f'<span class="meta">Был(а): {esc(dt_local(c["last_visit"], tz, "%d.%m.%Y"))}</span>'
            f"{actions}{windows}</div>"
        )
    if cards:
        body = '<div class="m-grid">' + "".join(cards) + "</div>"
    else:
        body = '<div class="empty">Клиентов пока нет — они появятся после первых заявок.</div>'
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
        page_head(
            "Клиенты",
            f"Карточек: {len(rows)}. Опознание «своих» — по номеру телефона",
            salon, salons,
        )
        + flash_html(flash, flash_error)
        + body
        + add_form
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
    """Прайс плитками: кратко на карточке, правка — окном поверх.

    Карточка говорит, что за услуга и сколько стоит; цена, длительность
    и «в прайсе» меняются окном на чистом CSS — как у мастеров и клиентов,
    JS в панели нет. Бот читает эти же данные в диалоге.
    """
    csrf, may_edit = _ctx_parts(ctx, "pricing")
    _, may_add = _ctx_parts(ctx, "catalog")
    sid = esc(salon["id"]) if salon else ""
    back = f"/services?salon={sid}"
    cards = []
    for s in rows:
        sv = esc(s["id"])
        # ── кратко на плитке: название, цена, длительность ───────────────
        off = "" if s["is_active"] else badge("в архиве", "gray")
        buttons, windows = "", ""
        if may_edit:
            buttons = f'<label class="btn" for="sv-{sv}">Правка</label>'
            windows = f'<input type="checkbox" id="sv-{sv}" class="modal-toggle">'
            windows += _service_window(s, sv, csrf, back)
        if may_add:
            buttons += action_form(
                "service_archive", csrf, back,
                f'<input type="hidden" name="service_id" value="{sv}">'
                f'<input type="hidden" name="active" value="{"0" if s["is_active"] else "1"}">',
                "Вернуть в прайс" if not s["is_active"] else "В архив",
            )
        actions = f'<div class="m-actions">{buttons}</div>' if buttons else ""
        cards.append(
            '<div class="m-card">'
            f'<div class="m-top"><b>{esc(s["title"])}</b>{off}</div>'
            f'<span class="meta">{s["duration_min"]} мин · {money(s["price"])}'
            + (" · в прайсе" if s["is_active"] else "")
            + "</span>"
            f"{actions}{windows}</div>"
        )
    if cards:
        body = '<div class="m-grid">' + "".join(cards) + "</div>"
    else:
        body = '<div class="empty">Прайс не загружен: docker compose run --rm cli python cli.py --seed</div>'
    add_form = ""
    if may_add:
        add_form = (
            '<form method="post" action="/actions" class="stack add-row">'
            '<input type="hidden" name="action" value="service_create">'
            f'<input type="hidden" name="back" value="{back}">'
            f"{csrf_field(csrf)}"
            '<div class="cell wide"><span>Новая услуга</span>'
            '<input type="text" name="title" placeholder="название" required></div>'
            '<div class="cell"><span>Цена, ₽</span>'
            '<input type="number" name="price" min="0" step="50" required></div>'
            '<div class="cell"><span>Длительность, мин</span>'
            '<input type="number" name="duration_min" value="60" min="5" max="600" '
            'step="5" required></div>'
            '<div class="cell save-cell"><span>&nbsp;</span>'
            '<button type="submit">Добавить</button></div>'
            "</form>"
        )
    revenue = sum(float(s["price"] or 0) for s in rows)
    content = (
        page_head(
            "Прайс",
            f"Услуг: {len(rows)}, сумма позиций: {money(revenue)}. "
            "Изменения сразу слышит бот в разговоре с клиентом",
            salon, salons,
        )
        + flash_html(flash, flash_error)
        + body
        + add_form
    )
    return layout("Прайс", "/services", content, salon=salon, salons=salons, role=_role(ctx))


def _service_window(
    s: dict[str, Any],
    sv: str,
    csrf: str,
    back: str,
) -> str:
    """Окно правки услуги: цена, длительность и «в прайсе» — одной формой.

    Тот же приём, что у мастеров и клиентов: чекбокс-тумблер и
    sibling-селектор, JS в панели нет. «В архив» — отдельная кнопка
    на плитке (право catalog, не pricing).
    """
    checked = " checked" if s["is_active"] else ""
    return (
        '<div class="modal">'
        f'<label class="modal-veil" for="sv-{sv}"></label>'
        '<form method="post" action="/actions" class="modal-card">'
        '<div class="modal-head">'
        f'<b>Услуга — {esc(s["title"])}</b>'
        f'<label class="modal-x" for="sv-{sv}">✕</label></div>'
        '<div class="modal-body stack">'
        '<label class="field"><span>Цена, ₽</span>'
        f'<input type="number" name="price" value="{int(s["price"] or 0)}" '
        'step="50" min="0"></label>'
        '<label class="field"><span>Длительность, мин</span>'
        f'<input type="number" name="duration_min" value="{s["duration_min"]}" '
        'step="5" min="5" max="600"></label>'
        '<label class="check">'
        f'<input type="checkbox" name="is_active"{checked}>в прайсе</label>'
        "</div>"
        '<div class="modal-foot">'
        '<input type="hidden" name="action" value="service_save">'
        f'<input type="hidden" name="service_id" value="{sv}">'
        f'<input type="hidden" name="back" value="{back}">'
        f"{csrf_field(csrf)}"
        f'<label class="btn ghost" for="sv-{sv}">Отмена</label>'
        '<button type="submit">Сохранить</button></div></form></div>'
    )


def page_masters(
    rows: list[dict[str, Any]],
    salon: dict | None,
    salons: list[dict] | None,
    ctx: Any | None = None,
    flash: str = "",
    flash_error: bool = False,
    services: list[dict[str, Any]] | None = None,
) -> str:
    """Мастера плитками: что делает, когда работает, и две кнопки — график и услуги.

    Обе настройки открываются окном поверх страницы (чистый CSS, JS в панели
    нет): плитка остаётся краткой, а не семью строками формы, как было.
    """
    from .queries import WEEKDAY_RU

    csrf, may_edit = _ctx_parts(ctx, "schedule")
    _, may_add = _ctx_parts(ctx, "catalog")
    sid = esc(salon["id"]) if salon else ""
    back = f"/masters?salon={sid}"
    services = services or []
    cards = []
    for m in rows:
        mid = esc(m["id"])
        hours_raw = m["hours"]
        if isinstance(hours_raw, str):
            hours_raw = json.loads(hours_raw or "[]")
        svc_raw = m.get("services") or []
        if isinstance(svc_raw, str):
            svc_raw = json.loads(svc_raw or "[]")
        # ── кратко на плитке: чем занимается и когда работает ──────────
        titles = [str(s["title"]) for s in svc_raw]
        work = ", ".join(titles[:3])
        if len(titles) > 3:
            work += f" +{len(titles) - 3}"
        summary = ", ".join(
            f"{WEEKDAY_RU[int(h['weekday'])]} {str(h['start'])[:5]}–{str(h['end'])[:5]}"
            for h in sorted(hours_raw, key=lambda x: int(x["weekday"]))
        )
        # ── кнопки и окна настроек (только у роли с правом) ────────────
        buttons, windows = "", ""
        if may_edit:
            buttons += f'<label class="btn" for="mh-{mid}">График работы</label>'
            windows += f'<input type="checkbox" id="mh-{mid}" class="modal-toggle">'
            windows += _hours_window(m, hours_raw, mid, csrf, back)
        if may_add:
            buttons += f'<label class="btn" for="ms-{mid}">Услуги мастера</label>'
            windows += f'<input type="checkbox" id="ms-{mid}" class="modal-toggle">'
            windows += _services_window(m, svc_raw, services, mid, csrf, back)
        actions = f'<div class="m-actions">{buttons}</div>' if buttons else ""
        off = "" if m.get("is_active", True) else '<span class="badge b-gray">отключён</span>'
        cards.append(
            '<div class="m-card">'
            f'<div class="m-top"><b>{esc(m["name"])}</b>{off}</div>'
            f'<span class="meta">Делает: {esc(work or "услуги не назначены")}</span>'
            f'<span class="meta">Когда: {esc(summary or "график не задан")}</span>'
            f"{actions}{windows}</div>"
        )
    if cards:
        body = '<div class="m-grid">' + "".join(cards) + "</div>"
    else:
        body = '<div class="empty">Мастера не заведены.</div>'
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
        page_head(
            "Мастера",
            "График — источник слотов: сузили окно, время исчезло у клиентов",
            salon, salons,
        )
        + flash_html(flash, flash_error)
        + body
        + add_form
    )
    return layout("Мастера", "/masters", content, salon=salon, salons=salons, role=_role(ctx))


def _hours_window(
    m: dict[str, Any],
    hours_raw: list[dict[str, Any]],
    mid: str,
    csrf: str,
    back: str,
) -> str:
    """Окно графика: вся неделя одной формой, одна кнопка «Сохранить».

    Чекбокс дня выключен → день нерабочий (время в форме всё равно стоит
    по умолчанию — пригодится, когда включат обратно).
    """
    from .queries import WEEKDAY_RU

    by_day = {int(h["weekday"]): h for h in hours_raw}
    rows = []
    for wd in range(7):
        h = by_day.get(wd)
        checked = " checked" if h else ""
        start = str((h or {}).get("start", "10:00:00"))[:5]
        end = str((h or {}).get("end", "20:00:00"))[:5]
        rows.append(
            '<div class="wh-row">'
            f'<label class="check"><input type="checkbox" name="d{wd}" value="1"{checked}>'
            f"{WEEKDAY_RU[wd]}</label>"
            f'<input type="time" name="s{wd}" value="{start}">'
            f'<input type="time" name="e{wd}" value="{end}">'
            "</div>"
        )
    return (
        '<div class="modal">'
        f'<label class="modal-veil" for="mh-{mid}"></label>'
        '<form method="post" action="/actions" class="modal-card">'
        '<div class="modal-head">'
        f"<b>График — {esc(m['name'])}</b>"
        f'<label class="modal-x" for="mh-{mid}">✕</label></div>'
        '<div class="modal-body stack">'
        '<div class="wh-head"><span>День</span><span>с</span><span>по</span></div>'
        + "".join(rows)
        + "</div>"
        '<div class="modal-foot">'
        '<input type="hidden" name="action" value="hours_save_week">'
        f'<input type="hidden" name="master_id" value="{mid}">'
        f'<input type="hidden" name="back" value="{back}">'
        f"{csrf_field(csrf)}"
        f'<label class="btn ghost" for="mh-{mid}">Отмена</label>'
        '<button type="submit">Сохранить</button></div></form></div>'
    )


def _services_window(
    m: dict[str, Any],
    svc_raw: list[dict[str, Any]],
    services: list[dict[str, Any]],
    mid: str,
    csrf: str,
    back: str,
) -> str:
    """Окно услуг мастера: чекбоксы прайса, отмечено — то, что делает."""
    mine = {str(s["id"]) for s in svc_raw}
    items = []
    for s in services:
        checked = " checked" if str(s["id"]) in mine else ""
        title = esc(s["title"])
        if not s.get("is_active", True):
            title += ' <span class="badge b-gray">в архиве</span>'
        items.append(
            '<label class="check big">'
            f'<input type="checkbox" name="service_id" value="{esc(s["id"])}"{checked}>'
            f"{title}</label>"
        )
    body = (
        "".join(items)
        if items
        else '<div class="empty">В прайсе нет услуг — сначала добавьте их во вкладке «Прайс».</div>'
    )
    return (
        '<div class="modal">'
        f'<label class="modal-veil" for="ms-{mid}"></label>'
        '<form method="post" action="/actions" class="modal-card">'
        '<div class="modal-head">'
        f"<b>Услуги — {esc(m['name'])}</b>"
        f'<label class="modal-x" for="ms-{mid}">✕</label></div>'
        f'<div class="modal-body stack">{body}</div>'
        '<div class="modal-foot">'
        '<input type="hidden" name="action" value="master_services_save">'
        f'<input type="hidden" name="master_id" value="{mid}">'
        f'<input type="hidden" name="back" value="{back}">'
        f"{csrf_field(csrf)}"
        f'<label class="btn ghost" for="ms-{mid}">Отмена</label>'
        '<button type="submit">Сохранить</button></div></form></div>'
    )
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
        page_head(
            "Диалоги",
            "Что бот отвечает клиентам — источник для контроля качества",
            salon, salons,
        )
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
        page_head(
            "Аудит",
            "Каждое значимое действие пишется сюда — бот, человек или система",
            salon, salons,
        )
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
                f'<label class="btn" for="us-new" style="margin-top:12px">'
                "Добавить сотрудника</label>"
                '<input type="checkbox" id="us-new" class="modal-toggle">'
                '<div class="modal">'
                '<label class="modal-veil" for="us-new"></label>'
                '<form method="post" action="/actions" class="modal-card">'
                '<div class="modal-head"><b>Новый сотрудник</b>'
                '<label class="modal-x" for="us-new">✕</label></div>'
                '<div class="modal-body stack">'
                '<input type="hidden" name="action" value="user_create">'
                f'<input type="hidden" name="back" value="{back}">'
                f"{csrf_field(csrf)}"
                '<label class="field"><span>Логин (e-mail)</span>'
                '<input type="email" name="login" placeholder="recept@salon.ru" '
                "required></label>"
                '<label class="field"><span>Имя</span>'
                '<input type="text" name="name" placeholder="Как обращаться"></label>'
                f'<label class="field"><span>Роль</span><select name="role">{options}'
                "</select></label>"
                "</div>"
                '<div class="modal-foot">'
                '<label class="btn ghost" for="us-new">Отмена</label>'
                "<button type=\"submit\">Создать</button></div></form></div>"
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
        '<div class="page">'
        '<div class="page-head-inner">'
        + page_head("Настройки", "Параметры точки и доступ в панель", salon, salons)
        + "</div>"
        '<div class="page-body">'
        + secrets_block
        + card("Точка и доступ", table(["Параметр", "Значение"], body))
        + people
        + note
        + "</div></div>"
    )
    return layout(
        "Настройки", "/settings", content,
        salon=salon, salons=salons, role=_role(ctx), inner_scroll=True,
    )
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
    bot = data.get("bot") or {}
    bot_prev = data.get("bot_previous") or {}
    bot_rows = [
        ["Диалогов", str(bot.get("dialogs", 0)), str(bot_prev.get("dialogs", 0))],
        ["Закончились записью", str(bot.get("dialogs_with_booking", 0)),
         str(bot_prev.get("dialogs_with_booking", 0))],
        ["Конверсия в запись", f'{bot.get("conversion", 0)}%',
         f'{bot_prev.get("conversion", 0)}%'],
        ["Попросили живого человека", str(bot.get("escalations", 0)),
         str(bot_prev.get("escalations", 0))],
        ["Доля уходов к человеку", f'{bot.get("escalation_rate", 0)}%',
         f'{bot_prev.get("escalation_rate", 0)}%'],
        ["Сообщений клиента в среднем", str(bot.get("avg_turns", 0)),
         str(bot_prev.get("avg_turns", 0))],
        ["Записей через бота", str(bot.get("bookings_via_bot", 0)),
         str(bot_prev.get("bookings_via_bot", 0))],
    ]
    content = (
        '<div class="page">'
        '<div class="page-head-inner">'
        + page_head("Отчёты", "", salon, salons)
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
        + "</div>"
        '<div class="page-body">'
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
        + card(
            f"Качество бота: {label} и {prev_label}",
            table(["Показатель", label, prev_label], bot_rows)
            if bot.get("dialogs") else
            '<div class="empty">За период диалогов не было — метрики появятся,'
            " когда бот отработает хотя бы один диалог</div>",
        )
        + "</div></div>"
    )
    return layout(
        "Отчёты", "/reports", content,
        salon=salon, salons=salons, role=_role(ctx), inner_scroll=True,
    )


def page_billing(
    status: dict[str, Any], salon: dict | None, salons: list[dict] | None,
    history: list[dict[str, Any]],
    legal: dict[str, Any] | None = None, errors: list[str] | None = None,
    flash: str = "", flash_error: bool = False, ctx: Any | None = None,
) -> str:
    errors = list(errors or [])
    csrf = ctx.get("csrf", "") if isinstance(ctx, dict) else ""
    # Форму реквизитов показываем только своей команде: в ней названия полей,
    # и владельцу салона они видеть незачем.
    may_billing = _role(ctx) == "team"
    req = status.get("requisites") or {}
    paid_until = status.get("paid_until")
    expired = bool(status.get("expired"))
    tone = "yellow" if expired else ("accent" if status.get("is_trial") else "green")
    head = (
        f'<p class="sub">{esc(status.get("status_text") or "")}</p>'
        + '<div class="tiles">'
        + tile(f'{status.get("price", 0)} руб.', "в месяц", tone, "")
        + tile(
            f'{paid_until:%d.%m.%Y}' if paid_until else "—",
            "оплачено до", "" if not expired else "yellow", "",
        )
        + tile(str(status.get("plan") or ""), "тариф", "", "")
        + tile(
            f'{status.get("debt", 0):.0f} руб.', "долг сети",
            "yellow" if status.get("debt") else "", "",
        )
        + tile(
            f'{status.get("paid_amount", 0):.0f} руб.', "оплачено за месяц",
            "green", "",
        )
        + "</div>"
    )
    actions = (
        action_form("invoice_issue", ctx.get("csrf", "") if isinstance(ctx, dict) else "",
                    "/billing", "", "Выставить счёт")
        if isinstance(ctx, dict) else ""
    )
    rows = [
        [
            esc(str(row["period_start"])),
            f'{row["period_end"]}',
            f'{float(row["amount"] or 0):.0f} руб.',
            esc(STATUS_RU.get(str(row["status"]), str(row["status"]))),
            esc(str(row["paid_at"])[:10]) if row.get("paid_at") else "—",
        ]
        for row in history
    ]
    requisites_block = card("Реквизиты для оплаты (СБП)", (
        '<div class="empty">Задайте BILLING_* в docker/.env — реквизиты появятся здесь'
        "</div>"
        if not req.get("legal_name") else
        '<table><tbody>'
        + "".join(
            f"<tr><th>{esc(title)}</th><td>{esc(str(req.get(key) or '—'))}</td></tr>"
            for key, title in (
                ("legal_name", "Получатель"), ("inn", "ИНН"), ("kpp", "КПП"),
                ("bank", "Банк"), ("bik", "БИК"), ("account", "Р/с"),
                ("tg", "Вопросы"),
            )
            if req.get(key)
        )
        + "</tbody></table>"
        + '<p class="sub">Карты не храним, автосписания нет: салон платит переводом '
        "раз в месяц, оплату подтверждает менеджер.</p>"
    ))
    return layout(
        "Тариф и оплата", "/billing",
        page_head("Тариф и оплата", "", salon, salons) + flash_html(flash, flash_error) + head
        + (
            '<div class="row-end">'
            + '<a class="tab" href="/accounting.csv?kind=payments">Счета (CSV)</a> '
            + '<a class="tab" href="/accounting.csv?kind=debtors">Долги (CSV)</a> '
            + '<a class="tab" href="/accounting.csv?kind=clients">Клиенты (CSV)</a>'
            + actions + "</div>"
            if actions else ""
        )
        + card("История платежей", table(
            ["Период с", "Период по", "Сумма", "Статус", "Оплачен"], rows,
            "Платежей пока не было",
        ))
        + requisites_block
        + (_legal_card(legal, csrf, "/billing", errors) if may_billing else ""),
        salon=salon, salons=salons, role=_role(ctx),
    )


def _legal_card(
    legal: dict[str, Any] | None, csrf: str, back: str, errors: list[str],
) -> str:

    """Карточка плательщика: тип выбирается первым, поля — по типу.

    Показ полей сделан на CSS ``:has()`` без JavaScript: при смене типа браузер
    сам показывает нужный набор, а сервер всё равно проверяет только его — то
    есть скрытие полей не является защитой, а лишь удобством.
    """
    from admin_core.billing_legal import CLIENT_TYPES, fields_of  # noqa: PLC0415

    current = legal or {}
    client_type = str(current.get("client_type") or "ip")
    details = current.get("details") or {}
    if not isinstance(details, dict):
        details = {}
    sid = esc(current.get("salon_id", ""))
    types = "".join(
        f'<label class="radio"><input type="radio" name="client_type" '
        f'value="{esc(key)}"{" checked" if key == client_type else ""}> '
        f'{esc(title)}</label>'
        for key, title in CLIENT_TYPES.items()
    )
    blocks = []
    for key in CLIENT_TYPES:
        rows = []
        if key == client_type:
            rows.append(
                f'<label class="field"><span>ИНН ('
                f'{"10" if key == "llc" else "12"} цифр)</span>'
                f'<input name="inn" value="{esc(current.get("inn", ""))}" required></label>'
            )
        for field_key, title, required, hint in fields_of(key):
            rows.append(
                f'<label class="field"><span>{esc(title)}'
                f'{" *" if required else ""}</span>'
                f'<input name="{esc(field_key)}[{esc(key)}]" '
                f'value="{esc(str(details.get(field_key, "")))}"'
                f'{" required" if required else ""}>'
                + (f'<small>{esc(hint)}</small>' if hint else "")
                + "</label>"
            )
        blocks.append(
            f'<fieldset data-type="{esc(key)}">{"".join(rows)}</fieldset>'
        )
    return card(
        "Карточка плательщика (нужна для счёта)",
        (
            (f'<div class="flash error">{esc("; ".join(errors))}</div>' if errors else "")
            + '<p class="sub">Тип выбирается первым — дальше нужны только его поля. '
              "ИНН, ОГРН и ОГРНИП проверяются по контрольной сумме: ошибку в цифре "
              "банк отвергнет, и платёж потеряется.</p>"
            + f'<form method="post" action="/actions" class="legal-form">{csrf_field(csrf)}'
            + '<input type="hidden" name="action" value="legal_save">'
            + f'<input type="hidden" name="back" value="{esc(back)}">'
            + f'<input type="hidden" name="salon_id" value="{sid}">'
            + f'<div class="radios">{types}</div>'
            + "".join(blocks)
            + '<label class="field"><span>Выставлять счёт автоматически '
              f"каждый {esc(current.get('invoice_due_day', 1))} число</span>"
              '<select name="invoice_due_day">'
            + "".join(
                f'<option value="{day}"'
                f'{" selected" if int(current.get("invoice_due_day", 1)) == day else ""}>'
                f"{day} числа</option>"
                for day in (1, 5, 10, 15, 20, 25, 28)
            )
            + "</select></label>"
            + '<label class="check"><input type="checkbox" name="auto_invoice" value="1"'
            + (' checked' if current.get("auto_invoice", True) else "")
            + "> включено</label>"
            + '<label class="field"><span>Номер договора</span>'
            + f'<input name="contract_no" value="{esc(current.get("contract_no") or "")}">'
            + "</label>"
            + '<button type="submit" class="btn">Сохранить карточку</button>'
            + "</form>"
        ),
    )


STATUS_RU = {
    "issued": "выставлен", "paid": "оплачен",
    "overdue": "просрочен", "void": "аннулирован",
}


STATUS_RU = {
    "issued": "выставлен", "paid": "оплачен",
    "overdue": "просрочен", "void": "аннулирован",
}


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
