"""Страницы панели: «Сегодня» и «Записи».

«Сегодня» — главный экран владельца точки: записи, деньги, вклад бота,
диалоги и загрузка мастеров. Дальше идёт «Записи» с фильтром по статусу.
"""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

from .render import badge, card, dt_local, esc, layout, money, table, tile


def _status_badge(status: str) -> str:
    from .queries import STATUS_RU, STATUS_TONE

    return badge(STATUS_RU.get(status, status), STATUS_TONE.get(status, "gray"))


def page_today(
    stats: dict[str, Any],
    schedule: list[dict[str, Any]],
    tz: ZoneInfo,
    salon: dict | None,
    salons: list[dict] | None,
) -> str:
    """Дашборд дня: плитки сверху, расписание снизу."""
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
    rows = [
        [
            dt_local(b["starts_at"], tz),
            esc(b["client_name"] or "Без имени"),
            esc(b["client_phone"] or ""),
            esc(b["service_title"]),
            esc(b["master_name"]),
            money(b["price"]),
            _status_badge(b["status"]),
        ]
        for b in schedule
    ]
    content = (
        f"<h1>Сегодня</h1>"
        f'<p class="sub">{esc(salon["name"] if salon else "")} — '
        f'подтверждено {stats["confirmed"]}, выполнено {stats["completed"]}, '
        f'отменено {stats["cancelled"]}</p>'
        f'<div class="tiles">{tiles}</div>'
        + card("Загрузка", load)
        + card(
            "Расписание на сегодня",
            table(
                ["Время", "Клиент", "Телефон", "Услуга", "Мастер", "Цена", "Статус"],
                rows,
                "На сегодня записей нет. Бот продолжает принимать заявки.",
            ),
        )
    )
    return layout("Сегодня", "/today", content, salon=salon, salons=salons)


def page_bookings(
    rows: list[dict[str, Any]],
    tz: ZoneInfo,
    status: str,
    salon: dict | None,
    salons: list[dict] | None,
) -> str:
    """Лента записей за ±30 дней с фильтром по статусу (ссылками, без формы)."""
    from .queries import SOURCE_RU, STATUS_RU

    sid = salon["id"] if salon else ""
    statuses = ["", *STATUS_RU]
    links = ['<a class="btn" style="margin-right:6px" href="/bookings?salon=' + esc(sid) + '">Все</a>']
    for code in statuses[1:]:
        cls = "btn" if code != status else ""
        style = ' style="background:var(--accent-bg);color:var(--text)"' if code == status else ""
        links.append(
            f'<a class="{cls}"{style} style="margin-right:6px" '
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
        ]
        for b in rows
    ]
    content = (
        "<h1>Записи</h1>"
        '<p class="sub">Период: 30 дней назад и 30 дней вперёд</p>'
        f'<div class="row" style="margin-bottom:14px">{"".join(links)}</div>'
        + card(
            f"Найдено: {len(rows)}",
            table(
                ["Когда", "Клиент", "Телефон", "Услуга", "Мастер", "Цена", "Источник", "Статус"],
                table_rows,
                "Под выбранный фильтр записей нет.",
            ),
        )
    )
    return layout("Записи", "/bookings", content, salon=salon, salons=salons)
