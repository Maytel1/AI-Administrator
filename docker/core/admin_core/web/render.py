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


def layout(
    title: str,
    active: str,
    content: str,
    *,
    salon: dict | None = None,
    salons: list[dict] | None = None,
) -> str:
    """Страница целиком: сайдбар с активным разделом и переключатель салона."""
    sid = salon["id"] if salon else ""
    nav = "".join(
        f'<a href="{path}?salon={esc(sid)}" class="{"active" if path == active else ""}">'
        f"<i>{icon}</i>{esc(name)}</a>"
        for path, name, icon in NAV
    )
    switch = ""
    if salons:
        opts = "".join(
            f'<option value="{esc(s["id"])}"{" selected" if sid == str(s["id"]) else ""}>'
            f'{esc(s["name"])}</option>'
            for s in salons
        )
        switch = (
            '<form method="get" action="/salon" style="min-width:210px">'
            f'<select name="id" onchange="this.form.submit()">{opts}</select></form>'
        )
    return (
        '<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{esc(title)} — Дежурный24</title>"
        '<link rel="stylesheet" href="/static.css"></head><body>'
        '<div class="shell"><aside><div class="brand">Дежурный<span>24</span></div>'
        f"<nav>{nav}</nav>"
        '<div class="foot">MVP · read-only<br>фаза 4</div></aside>'
        f'<main><div class="top">{content}{switch}</div></main>'
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
