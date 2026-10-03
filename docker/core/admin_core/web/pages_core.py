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
) -> str:
    """Дашборд дня: плитки сверху, расписание снизу."""
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
        f'<div class="row" style="justify-content:space-between">'
        f'<span>Мастера в работе сегодня</span>'
        f'<span>{stats["busy_masters"]} из {stats["masters_total"]}</span></div>'
        f'<div class="bar"><div style="width:{load_pct}%"></div></div>'
    )
    sid = f"?salon={esc(salon['id'])}" if salon else ""
    rows = [
        [
            dt_local(b["starts_at"], tz),
            esc(b["client_name"] or "Без имени"),
            esc(b["client_phone"] or ""),
            esc(b["service_title"]),
            esc(b["master_name"]),
            money(b["price"]),
            _status_badge(b["status"]),
            _status_buttons(str(b["id"]), b["status"], csrf, may_status, f"/today{sid}"),
            _prepayment_buttons(
                str(b["id"]), b.get("prepayment_status") or "not_required",
                csrf, f"/today{sid}", may_edit=may_status,
            )
            + _reschedule_form(str(b["id"]), csrf if may_status else "", f"/today{sid}"),
        ]
        for b in schedule
    ]
    content = (
        f"<h1>Сегодня</h1>"
        f'<p class="sub">{esc(salon["name"] if salon else "")} — '
        f'подтверждено {stats["confirmed"]}, выполнено {stats["completed"]}, '
        f'отменено {stats["cancelled"]}</p>'
        + flash_html(flash, flash_error)
        + f'<div class="tiles">{tiles}</div>'
        + card("Загрузка", load)
        + card(
            "Расписание на сегодня",
            table(
                ["Время", "Клиент", "Телефон", "Услуга", "Мастер", "Цена", "Статус", "Действия"],
                rows,
                "На сегодня записей нет. Бот продолжает принимать заявки.",
            ),
        )
    )
    return layout("Сегодня", "/today", content, salon=salon, salons=salons, role=_role(ctx))


def page_bookings(
    rows: list[dict[str, Any]],
    tz: ZoneInfo,
    status: str,
    salon: dict | None,
    salons: list[dict] | None,
    ctx: Any | None = None,
    flash: str = "",
    flash_error: bool = False,
) -> str:
    """Лента записей за ±30 дней с фильтром по статусу (ссылками, без формы)."""
    from .queries import SOURCE_RU, STATUS_RU

    csrf, may_status = _ctx_parts(ctx, "booking_status")
    sid = salon["id"] if salon else ""
    back = f"/bookings?salon={esc(sid)}" + (f"&status={esc(status)}" if status else "")
    statuses = ["", *STATUS_RU]
    links = [
        '<a class="btn" style="margin-right:6px" href="/bookings?salon=' + esc(sid) + '">Все</a>'
    ]
    for code in statuses[1:]:
        style = ' style="background:var(--accent-bg);color:var(--text)"' if code == status else ""
        links.append(
            f'<a class="btn"{style} style="margin-right:6px" '
            f'href="/bookings?salon={esc(sid)}&status={esc(code)}">{esc(STATUS_RU[code])}</a>'
        )
    table_rows = [
        [
            dt_local(b["starts_at"], tz, "%d.%m %H:%M"),
            esc(b["client_name"] or "Без имени"),
            esc(b["client_phone"] or ""),
            esc(b["service_title"]),
            esc(b["master_name"]),
            money(b["price"]),
            esc(SOURCE_RU.get(b["source"], b["source"])),
            _status_badge(b["status"]),
            _status_buttons(str(b["id"]), b["status"], csrf, may_status, back),
            _prepayment_buttons(
                str(b["id"]), b.get("prepayment_status") or "not_required",
                csrf, back, may_edit=may_status,
            )
            + _reschedule_form(str(b["id"]), csrf if may_status else "", back),
        ]
        for b in rows
    ]
    content = (
        "<h1>Записи</h1>"
        '<p class="sub">Период: 30 дней назад и 30 дней вперёд</p>'
        + flash_html(flash, flash_error)
        + f'<div class="row" style="margin-bottom:14px">{"".join(links)}</div>'
        + card(
            f"Найдено: {len(rows)}",
            table(
                ["Когда", "Клиент", "Телефон", "Услуга", "Мастер", "Цена", "Источник",
                 "Статус", "Действия"],
                table_rows,
                "Под выбранный фильтр записей нет.",
            ),
        )
    )
    return layout("Записи", "/bookings", content, salon=salon, salons=salons, role=_role(ctx))
