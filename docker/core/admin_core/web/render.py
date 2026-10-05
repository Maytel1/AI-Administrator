"""Общий каркас HTML панели: сайдбар, плитки, таблицы, бейджи.

Каркас собирается строками — без шаблонизатора: панели не нужна лишняя
зависимость, а весь текст всё равно проходит через ``html.escape``.
Стиль — ``app/static.css``, отдаётся сервисом отдельным маршрутом.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any, Iterable
from zoneinfo import ZoneInfo

NAV = [
    ("/today", "Сегодня", "◎"),
    ("/bookings", "Записи", "▦"),
    ("/clients", "Клиенты", "☺"),
    ("/services", "Прайс", "₽"),
    ("/masters", "Мастера", "✂"),
    ("/dialogs", "Диалоги", "✉"),
    ("/reports", "Отчёты", "◷"),
    ("/network", "Сеть", "◈"),
    ("/billing", "Тариф и оплата", "€"),
    ("/audit", "Аудит", "⚑"),
    ("/settings", "Настройки", "⚙"),
]


def esc(value: Any) -> str:
    """Экранирование: в панель попадают имена и телефоны клиентов (ПДн)."""
    return escape("" if value is None else str(value), quote=True)


def money(value: Any) -> str:
    """1200.0 → «1 200 ₽» — разряды неразрывным пробелом, как принято в прайсах."""
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        return "—"
    return f"{int(round(n)):,}".replace(",", " ") + " ₽"


def dt_local(value: Any, tz: ZoneInfo, fmt: str = "%H:%M") -> str:
    """Время из БД (aware UTC) → местное время салона."""
    if value is None:
        return "—"
    if not isinstance(value, datetime):
        return str(value)
    return value.astimezone(tz).strftime(fmt)


def badge(text: str, tone: str = "gray") -> str:
    return f'<span class="badge b-{tone}">{esc(text)}</span>'


def tile(value: str, label: str, tone: str = "", extra: str = "") -> str:
    """Плитка показателя. ``extra`` — необязательная подпись снизу (тренд, сноска)."""
    style = f' style="color:var(--{tone})"' if tone else ""
    foot = f"{extra}" if extra else ""
    return (
        f'<div class="tile"><b{style}>{esc(value)}</b><span>{esc(label)}</span>'
        f"{foot}</div>"
    )


def card(title: str, body: str) -> str:
    return f'<div class="card"><h2>{esc(title)}</h2>{body}</div>'


def table(headers: Iterable[str], rows: list[list[str]], empty: str = "Пока пусто") -> str:
    """Пустую таблицу заменяем подсказкой — пустая сетка выглядит как ошибка."""
    if not rows:
        return f'<div class="empty">{esc(empty)}</div>'
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def flash(message: str, error: bool = False) -> str:
    """Результат правки: зелёный (сделано) или красный (не получилось)."""
    if not message:
        return ""
    tone = "ok" if not error else "err"
    return f'<div class="flash {tone}">{esc(message)}</div>'


def csrf_field(csrf: str) -> str:
    """Скрытое поле CSRF: без него чужая страница не сможет отправить форму."""
    return f'<input type="hidden" name="csrf" value="{esc(csrf)}">'


def action_form(
    action: str, csrf: str, back: str, fields: str, label: str = "Сохранить"
) -> str:
    """Форма правки: действие + CSRF + поля + возврат на исходную страницу."""
    return (
        f'<form method="post" action="/actions" class="inline">'
        f'<input type="hidden" name="action" value="{esc(action)}">'
        f'<input type="hidden" name="back" value="{esc(back)}">'
        f"{csrf_field(csrf)}{fields}"
        f'<button type="submit">{esc(label)}</button></form>'
    )


def page_head(
    title: str,
    sub: str = "",
    salon: dict | None = None,
    salons: list[dict] | None = None,
) -> str:
    """Единая шапка вкладки: заголовок с пояснением и переключатель салона.

    Раньше каждая страница писала `<h1>` и `<p class=sub>` сама, а
    переключатель салона рисовал layout справа от всего содержимого — из-за
    чего шапки разъезжались, а на узких экранах переключатель уезжал вбок.
    Теперь вид одинаковый везде, как на «Сегодня».
    """
    return (
        '<div class="page-head">'
        f'<div class="page-head-text"><h1>{esc(title)}</h1>'
        + (f'<p class="sub">{sub}</p>' if sub else "")
        + "</div>"
        + salon_switch(salon, salons)
        + "</div>"
    )


def salon_switch(salon: dict | None, salons: list[dict] | None) -> str:
    """Переключатель салона. Отдельная функция, а не кусок layout:

    на «Сегодня» он должен стоять под заголовком и полосой загрузки, а не
    справа от всего содержимого.
    """
    if not salons:
        return ""
    sid = salon["id"] if salon else ""
    opts = "".join(
        f'<option value="{esc(s["id"])}"{" selected" if sid == str(s["id"]) else ""}>'
        f'{esc(s["name"])}</option>'
        for s in salons
    )
    return (
        '<form method="get" action="/salon" class="salon-switch">'
        f'<select name="id" onchange="this.form.submit()">{opts}</select></form>'
    )


def layout(
    title: str,
    active: str,
    content: str,
    *,
    salon: dict | None = None,
    salons: list[dict] | None = None,
    role: str = "",
    inner_scroll: bool = False,
    switch_inside: bool = True,
) -> str:
    """Страница целиком: сайдбар с активным разделом и переключатель салона.

    `inner_scroll=True` запрещает прокрутку самой страницы: прокручивается
    только содержимое внутри `.page-scroll`. Нужен там, где блоков много и
    страницу иначе приходится катать колесом мимо шапки.
    """
    sid = salon["id"] if salon else ""
    # «Сеть» видит только роль нашей команды: владельцу точки показывать
    # чужую выручку незачем, а ссылка в меню была бы враньём в интерфейсе.
    nav_items = [
        item for item in NAV
    # /network и /billing видны только нашей команне: счета и сводка по сети
    # — не клиентские данные салона.
    if item[0] not in ("/network", "/billing") or (role and role == "team")
    ]
    nav = "".join(
        f'<a href="{path}?salon={esc(sid)}" class="{"active" if path == active else ""}">'
        f"<i>{icon}</i>{esc(name)}</a>"
        for path, name, icon in nav_items
    )
    switch = salon_switch(salon, salons) if not switch_inside else ""
    return (
        '<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{esc(title)} — Дежурный24</title>"
        '<link rel="stylesheet" href="/static.css"></head><body>'
        '<div class="shell"><aside><div class="brand">Дежурный<span>24</span></div>'
        f"<nav>{nav}</nav>"
        '<div class="foot">MVP · read-only<br>фаза 4</div></aside>'
        f'<main class="{"inner" if inner_scroll else ""}">'
        # Контент обязательно в своей обёртке: .top — flex, и без неё все
        # блоки страницы становились его детьми и выстраивались в один ряд
        # (расписание вставало справа от плиток вместо того, чтобы быть под ними).
        f'<div class="top"><div class="content">{content}</div>{switch}</div></main>'
        "</div></body></html>"
    )


def login_page(error: str = "", *, login: str = "", need_totp: bool = False) -> str:
    """Вход в панель: логин + пароль (+ код 2FA, если у пользователя включена)."""
    msg = f'<div class="flash err">{esc(error)}</div>' if error else ""
    totp_field = ""
    if need_totp:
        totp_field = (
            '<label for="code">Код из приложения (6 цифр)</label>'
            '<input id="code" name="code" type="text" inputmode="numeric" '
            'pattern="[0-9]{6}" maxlength="6" autofocus required>'
        )
    return (
        '<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Вход — Дежурный24</title>"
        '<link rel="stylesheet" href="/static.css"></head><body>'
        '<div class="login"><form method="post" action="/login">'
        '<div class="brand" style="padding:0 0 6px">Дежурный<span>24</span></div>'
        '<div class="sub">Панель управления точкой</div>'
        f"{msg}"
        '<label for="login">Логин (e-mail)</label>'
        f'<input id="login" name="login" type="email" value="{esc(login)}" '
        f"{'autofocus' if not need_totp else ''} required>"
        '<label for="pwd">Пароль</label>'
        '<input id="pwd" name="password" type="password" required>'
        f"{totp_field}"
        '<div style="margin-top:18px"><button type="submit" style="width:100%">Войти</button></div>'
        "</form></div></body></html>"
    )
