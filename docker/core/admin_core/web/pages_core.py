"""Страницы панели: «Сегодня» и «Записи».

«Сегодня» — главный экран владельца точки: записи, деньги, вклад бота,
диалоги и загрузка мастеров. Дальше идёт «Записи» с фильтром по статусу.
"""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

from .render import (
    action_form,
    badge,
    csrf_field,
    dt_local,
    esc,
    layout,
    money,
    page_head,
    salon_switch,
    tile,
)
from .render import (
    flash as flash_html,
)


def _role(ctx: Any | None) -> str:
    """Роль посетителя для каркаса: что показывать в меню.

    Берётся из контекста запроса, а не из формы: иначе ссылку в меню можно
    было бы выдать себе за чужую роль параметром.
    """
    return str(getattr(ctx, "role", "") or "")
def _ctx_parts(ctx: Any | None, permission: str) -> tuple[str, bool]:
    """Достаём csrf и право на правку из PageCtx.

    None (тест или гость) → ничего не рисуем: страница без мёртвых кнопок.
    """
    if ctx is None:
        return "", False
    may = getattr(ctx, "may", None)
    allowed = bool(may(permission)) if callable(may) else False
    return getattr(ctx, "csrf", "") or "", allowed


def _status_badge(status: str) -> str:
    from .queries import STATUS_RU, STATUS_TONE

    return badge(STATUS_RU.get(status, status), STATUS_TONE.get(status, "gray"))


PERIOD_LABELS: tuple[tuple[int, str], ...] = (
    (1, "Сегодня"),
    (7, "Неделя"),
    (30, "Месяц"),
    (90, "Квартал"),
)


def _period_picker(days: int, salon_id: str, status: str) -> str:
    """Выбор периода: одна кнопка, по клику — список.

    Сделано на `<details>`: панель живёт под CSP с ``script-src 'self'``, то
    есть инлайновый JavaScript там запрещён. `<details>` открывается без
    единой строки скрипта и работает во всех браузерах, которыми пользуется
    администратор салона.

    Список подписан тем периодом, который реально выбран, а не «30 дней»:
    расхождение между подписью и фильтром выглядит как ошибка программы.
    """
    label = next((name for value, name in PERIOD_LABELS if value == days), "Месяц")
    items = []
    for value, name in PERIOD_LABELS:
        # & в атрибуте href обязан быть &amp;: без этого HTML валиден
        # «почти всегда», а валидатор и часть браузеров ломают разметку.
        url = (
            f"/bookings?salon={esc(salon_id)}&amp;days={value}"
            + (f"&amp;status={esc(status)}" if status else "")
        )
        mark = ' aria-current="true"' if value == days else ""
        dot = '<span class="tick">✓</span>' if value == days else "<span></span>"
        items.append(
            f'<li><a href="{url}"{mark}>{dot}{esc(name)}</a></li>'
        )
    return (
        '<details class="picker">'
        f'<summary>Период: {esc(label)}<span class="caret"></span></summary>'
        f'<ul class="picker-list">{"".join(items)}</ul>'
        "</details>"
    )


def _action_menu(
    booking_id: str, status: str, csrf: str, may_status: bool, back: str,
    *, prepay_status: str = "not_required",
) -> str:
    """Меню действий по записи: одно мини-окно вместо россыпи кнопок.

    Раньше в ячейке стояли три кнопки смены статуса, отметка предоплаты и поле
    переноса — строка разъезжалась, а в шапке таблицы для этих ячеек не было
    даже заголовков. Теперь всё это живёт в одном меню: выполнена, отменена,
    перенести (со вторым мини-окном на дату и время) и отметка предоплаты.

    Предоплата уехала сюда же, а не исчезла: без неё администратор не смог
    бы сказать «клиент оплатил вперёд», а это влияет на напоминания.
    """
    if not csrf or not may_status:
        return '<span class="muted-cell">—</span>'
    if status in ("completed", "cancelled"):
        return '<span class="muted-cell">закрыта</span>'

    hidden = f'<input type="hidden" name="booking_id" value="{esc(booking_id)}">'
    options = [
        ("completed", "Выполнена", "ok"),
        ("cancelled", "Отменена", "danger"),
    ]
    if status != "confirmed":
        options.insert(0, ("confirmed", "Подтверждена", ""))
    items = []
    for code, name, kind in options:
        form = (
            f'<form method="post" action="/actions" class="menu-item">'
            f'<input type="hidden" name="action" value="booking_status">'
            f'{hidden}<input type="hidden" name="back" value="{esc(back)}">'
            f'<input type="hidden" name="status" value="{code}">'
            f"{csrf_field(csrf)}"
            f'<button type="submit" class="{kind}">{esc(name)}</button>'
            "</form>"
        )
        items.append(form)

    reschedule = (
        f'<form method="post" action="/actions" class="menu-item">'
        f'<input type="hidden" name="action" value="booking_reschedule">'
        f'{hidden}<input type="hidden" name="back" value="{esc(back)}">'
        f"{csrf_field(csrf)}"
        '<label class="menu-label">Перенести на</label>'
        '<input type="datetime-local" name="new_start" required class="menu-input">'
        '<button type="submit" class="primary">Перенести</button>'
        "</form>"
    )

    # Предоплата: статусы те же, что были в отдельной ячейке (004),
    # иначе пункт «в меню» оказался бы кнопкой в никуда.
    prepay = _prepayment_buttons(booking_id, prepay_status, csrf, back)

    return (
        '<details class="menu">'
        '<summary class="menu-trigger" aria-label="Действия по записи">Действия</summary>'
        f'<div class="menu-pop">{"".join(items)}{prepay}{reschedule}</div>'
        "</details>"
    )


def _status_buttons(
    booking_id: str, status: str, csrf: str, may_status: bool, back: str
) -> str:
    """Кнопки смены статуса: только если роль действительно может их нажать."""
    if not csrf or not may_status or status in ("completed", "cancelled"):
        return "—"
    hidden = f'<input type="hidden" name="booking_id" value="{esc(booking_id)}">'
    buttons = []
    for code, label in (
        ("confirmed", "Подтвердить"), ("completed", "Выполнена"), ("cancelled", "Отмена"),
    ):
        if code == status:
            continue
        buttons.append(
            action_form("booking_status", csrf, back, hidden
                        + f'<input type="hidden" name="status" value="{code}">',
                        label)
        )
    return " ".join(buttons)


def _reschedule_form(booking_id: str, csrf: str, back: str) -> str:
    """Форма переноса: одно поле с датой и временем.

    Отдельная форма, а не кнопка: перенос требует указать новое время, а лишняя
    кнопка без поля только путает — «перенести» куда?
    """
    if not csrf:
        return ""
    return (
        f'<form method="post" action="/actions" class="inline">'
        f'<input type="hidden" name="action" value="booking_reschedule">'
        f'<input type="hidden" name="booking_id" value="{esc(booking_id)}">'
        f'<input type="hidden" name="back" value="{esc(back)}">'
        f"{csrf_field(csrf)}"
        '<input type="datetime-local" name="new_start" required '
        'style="background:var(--surface-2);color:var(--text);border:1px solid '
        'var(--border);border-radius:6px;padding:4px 6px;font-size:12px">'
        '<button type="submit" class="btn-mini">Перенести</button>'
        "</form>"
    )


def _prepayment_buttons(
    booking_id: str, status: str, csrf: str, back: str, *, may_edit: bool = True
) -> str:
    """Приём предоплаты из панели: статусы из 004 иначе остались бы мёртвыми."""
    if not csrf or not may_edit or status == "not_required":
        return ""
    hidden = f'<input type="hidden" name="booking_id" value="{esc(booking_id)}">'
    buttons = []
    for code, label in (("paid", "Оплачено"), ("offered", "Ждём оплату")):
        if code == status:
            continue
        buttons.append(
            action_form("prepayment_set", csrf, back,
                        hidden + f'<input type="hidden" name="prepayment_status" value="{code}">',
                        label)
        )
    return " ".join(buttons)


def page_today(
    stats: dict[str, Any],
    schedule: list[dict[str, Any]],
    tz: ZoneInfo,
    salon: dict | None,
    salons: list[dict] | None,
    ctx: Any | None = None,
    flash: str = "",
    flash_error: bool = False,
    onboarding: dict[str, Any] | None = None,
) -> str:
    """Дашборд дня: плитки сверху, расписание снизу."""
    from .onboarding import onboarding_card

    csrf, may_status = _ctx_parts(ctx, "booking_status")
    load_pct = round(
        100 * stats["busy_masters"] / stats["masters_total"] if stats["masters_total"] else 0
    )
    tiles = "".join(
        [
            tile(str(stats["total"]), "записей на сегодня"),
            tile(money(stats["revenue"]), "выручка дня", "green"),
            tile(f'{stats["from_bot"]}/{stats["total"]}', "записей сделал бот", "accent"),
            tile(str(stats["dialogs"]), "диалогов сегодня"),
            tile(str(stats["new_clients"]), "новых клиентов"),
            tile(f'{stats["week"]}', "записей на 7 дней вперед"),
        ]
    )
    load = (
        f'<div class="load-line">'
        f'<span class="load-title">Мастера в работе сегодня</span>'
        f'<span class="load-count">{stats["busy_masters"]} из {stats["masters_total"]}</span>'
        f'<div class="bar"><div style="width:{load_pct}%"></div></div>'
        f"</div>"
    )
    sid = f"?salon={esc(salon['id'])}" if salon else ""
    cards = [
        '<div class="m-card">'
        f'<div class="m-top"><b>{esc(dt_local(b["starts_at"], tz))}</b>'
        + _status_badge(b["status"]) + "</div>"
        f'<span class="meta">{esc(b["client_name"] or "Без имени")}'
        f'{(" · " + esc(b["client_phone"])) if b["client_phone"] else ""}</span>'
        f'<span class="meta">{esc(b["service_title"])} · {esc(b["master_name"])}</span>'
        f'<span class="meta">{money(b["price"])}</span>'
        '<div class="m-actions">'
        + _action_menu(
            str(b["id"]), b["status"], csrf, may_status, f"/today{sid}",
            prepay_status=b.get("prepayment_status") or "not_required",
        )
        + "</div></div>"
        for b in schedule
    ]
    if cards:
        schedule_body = f'<div class="m-grid">{"".join(cards)}</div>'
    else:
        schedule_body = (
            '<div class="empty">На сегодня записей нет. '
            "Бот продолжает принимать заявки.</div>"
        )
    content = (
        # Слева заголовок, полоса загрузки и переключатель салона, справа —
        # узкие плитки в один ряд. Раньше плитки шли во всю ширину, а
        # переключатель салона висел справа от всего содержимого.
        '<div class="dash-top">'
        '<div class="dash-side">'
        "<h1>Сегодня</h1>"
        # Сводку «подтверждено N, выполнено N, отменено N» убрали: она
        # дублировала плитки и съедала высоту экрана.
        + load
        + salon_switch(salon, salons)
        + "</div>"
        + f'<div class="tiles tiles-row">{tiles}</div>'
        "</div>"
        + flash_html(flash, flash_error)
        # Чек-лист онбординга: только пока владелец не прошёл «первые шаги».
        # Прячем при 100% (onboarding_card вернёт "") — не мозолить глаза потом.
        + onboarding_card(onboarding, str(salon["id"]) if salon else "")
        + '<div class="panel panel-tight">'
        + '<div class="panel-head"><div class="panel-title"><h2 class="panel-h">'
        + "Расписание на сегодня</h2>"
        + f'<span class="count">Записей: {len(schedule)}</span></div></div>'
        + schedule_body
        + "</div>"
    )
    return layout(
        "Сегодня", "/today", content, salon=salon, salons=salons,
        role=_role(ctx), switch_inside=True,
    )


def page_bookings(
    rows: list[dict[str, Any]],
    tz: ZoneInfo,
    status: str,
    salon: dict | None,
    salons: list[dict] | None,
    ctx: Any | None = None,
    flash: str = "",
    flash_error: bool = False,
    days: int = 30,
) -> str:
    """Лента записей за выбранный период с фильтром по статусу.

    Период приходит из query и в подписи, и в запрос к базе: подпись «Неделя»
    при тридцатидневном фильтре была бы просто неправдой.
    """
    from .queries import SOURCE_RU, STATUS_RU

    csrf, may_status = _ctx_parts(ctx, "booking_status")
    sid = salon["id"] if salon else ""
    back = f"/bookings?salon={esc(sid)}" + (f"&status={esc(status)}" if status else "")
    statuses = ["", *STATUS_RU]
    links = [
        '<a class="btn" style="margin-right:6px" href="/bookings?salon=' + esc(sid) + '">Все</a>'
    ]
    for code in statuses[1:]:
        active = (
            ' style="margin-right:6px;background:var(--accent-bg);color:var(--text)"'
            if code == status else ' style="margin-right:6px"'
        )
        links.append(
            f'<a class="btn"{active} '
            f'href="/bookings?salon={esc(sid)}&status={esc(code)}">{esc(STATUS_RU[code])}</a>'
        )
    cards = [
        '<div class="m-card">'
        f'<div class="m-top"><b>{esc(dt_local(b["starts_at"], tz, "%d.%m %H:%M"))}</b>'
        + _status_badge(b["status"]) + "</div>"
        f'<span class="meta">{esc(b["client_name"] or "Без имени")}'
        f'{(" · " + esc(b["client_phone"])) if b["client_phone"] else ""}</span>'
        f'<span class="meta">{esc(b["service_title"])} · {esc(b["master_name"])}</span>'
        f'<span class="meta">{money(b["price"])} · '
        f"Источник: {esc(SOURCE_RU.get(b['source'], b['source']))}</span>"
        '<div class="m-actions">'
        + _action_menu(
            str(b["id"]), b["status"], csrf, may_status, back,
            prepay_status=b.get("prepayment_status") or "not_required",
        )
        + "</div></div>"
        for b in rows
    ]
    if cards:
        feed = f'<div class="m-grid">{"".join(cards)}</div>'
    else:
        feed = '<div class="empty">Под выбранный фильтр записей нет.</div>'
    content = (
        # Заголовок вкладки — общий для всех (page_head), а ниже панель с
        # фильтрами и карточками. Раньше «Записи» были единственной вкладкой
        # с заголовком внутри панели — вид отличался от остальных.
        page_head("Записи", "", salon, salons)
        + flash_html(flash, flash_error)
        + '<div class="panel panel-tight">'
        + '<div class="panel-head">'
        + '<div class="panel-title"><span class="count">Найдено: '
        + f"{len(rows)}</span></div>"
        + '<div class="panel-filters">'
        + "".join(links)
        + _period_picker(days, sid, status)
        + "</div></div>"
        + feed
        + "</div>"
    )
    return layout("Записи", "/bookings", content, salon=salon, salons=salons, role=_role(ctx))
