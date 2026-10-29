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

# SVG-иконки навигации сайдбара (16×16, stroke=currentColor наследует цвет текста).
# Каждая собрана из коротких строк, чтобы не превышать линию длины линтера.

ICON_TODAY = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"'
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<rect x="3" y="4" width="18" height="18" rx="2" ry="2"/>'
    '<line x1="16" y1="2" x2="16" y2="6"/>'
    '<line x1="8" y1="2" x2="8" y2="6"/>'
    '<line x1="3" y1="10" x2="21" y2="10"/>'
    '</svg>'
)

ICON_BOOKINGS = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"'
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<rect x="3" y="4" width="18" height="18" rx="2" ry="2"/>'
    '<line x1="16" y1="2" x2="16" y2="6"/>'
    '<line x1="8" y1="2" x2="8" y2="6"/>'
    '<line x1="3" y1="10" x2="21" y2="10"/>'
    '<polyline points="8 18 12 22 16 18"/>'
    '</svg>'
)

ICON_CLIENTS = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"'
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/>'
    '<circle cx="12" cy="7" r="4"/>'
    '</svg>'
)

ICON_SERVICES = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"'
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M20 6H4l1 11 4-7 4 7 4-7 4 7 1-11z"/>'
    '<line x1="12" y1="19" x2="12" y2="23"/>'
    '<line x1="12" y1="11" x2="12" y2="23"/>'
    '</svg>'
)

ICON_MASTERS = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"'
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<circle cx="12" cy="6" r="4"/>'
    '<path d="M5 20l2 2 4-4"/>'
    '<path d="M19 20l-2 2-4-4"/>'
    '<line x1="12" y1="10" x2="12" y2="20"/>'
    '<line x1="12" y1="4" x2="8.17" y2="8"/>'
    '<line x1="12" y1="4" x2="15.83" y2="8"/>'
    '</svg>'
)

ICON_DIALOGS = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"'
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>'
    '</svg>'
)

ICON_REPORTS = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"'
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<line x1="18" y1="20" x2="18" y2="10"/>'
    '<line x1="12" y1="20" x2="12" y2="4"/>'
    '<line x1="6" y1="20" x2="6" y2="14"/>'
    '</svg>'
)

ICON_NETWORK = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"'
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<circle cx="12" cy="12" r="3"/>'
    '<path d="M5 9c0 1 1 2 2 2s2-1 2-2-1-2-2-2-2 1-2 2"/>'
    '<circle cx="19" cy="5" r="2"/>'
    '<circle cx="5" cy="19" r="2"/>'
    '<path d="M12 12c0 1-1 2-2 2s-2-1-2-2 1-2 2-2 2 1 2 2"/>'
    '<path d="M5 9v7M17 16v-3M19 5l-7 7"/>'
    '</svg>'
)

ICON_PAY = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"'
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<rect x="1" y="4" width="22" height="16" rx="2" ry="2"/>'
    '<line x1="1" y1="10" x2="23" y2="10"/>'
    '</svg>'
)

ICON_BILLING = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"'
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
    '<polyline points="14 2 14 8 20 8"/>'
    '<line x1="16" y1="13" x2="8" y2="13"/>'
    '<line x1="16" y1="17" x2="8" y2="17"/>'
    '<polyline points="10 9 9 9 8 9"/>'
    '</svg>'
)

ICON_COUNTERPARTIES = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"'
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>'
    '<circle cx="9" cy="7" r="4"/>'
    '<path d="M23 21v-2a4 4 0 0 0-3-3.87"/>'
    '<path d="M16 3.13a4 4 0 0 1 0 7.75"/>'
    '</svg>'
)

ICON_AUDIT = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"'
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<circle cx="12" cy="12" r="10"/>'
    '<line x1="12" y1="2" x2="12" y2="22"/>'
    '<line x1="2" y1="12" x2="22" y2="12"/>'
    '</svg>'
)

ICON_SETTINGS = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"'
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<circle cx="12" cy="12" r="3"/>'
    '<path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0'
    ' l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0'
    ' 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1'
    ' -2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51'
    ' -1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33'
    ' -1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82'
    ' .33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0'
    ' 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06'
    ' .06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1'
    ' -2 2h-.09a1.65 1.65 0 0 0-1.51 1"/>'
    '</svg>'
)

NAV = [
    ("/today", "Сегодня", ICON_TODAY),
    ("/bookings", "Записи", ICON_BOOKINGS),
    ("/clients", "Клиенты", ICON_CLIENTS),
    ("/services", "Прайс", ICON_SERVICES),
    ("/masters", "Мастера", ICON_MASTERS),
    ("/dialogs", "Диалоги", ICON_DIALOGS),
    ("/reports", "Отчёты", ICON_REPORTS),
    ("/network", "Сеть", ICON_NETWORK),
    ("/pay", "Оплата", ICON_PAY),
    ("/billing", "Счета (команда)", ICON_BILLING),
    ("/counterparties", "Контрагенты", ICON_COUNTERPARTIES),
    ("/audit", "Аудит", ICON_AUDIT),
    ("/settings", "Настройки", ICON_SETTINGS),
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


def usage_bar(used: int, limit: int | None, percent: int | None) -> str:
    """Индикатор использования лимита ресурса: полоска + «занято из максимума».

    Цвет полоски: зелёный в норме, жёлтый с 80% (WARN_PERCENT — предупреждение),
    красный при исчерпании. Без лимита (None) — просто «занято, без ограничений».
    """
    if limit is None or percent is None:
        return f'<span class="sub">{int(used)} без ограничений</span>'
    tone = "accent"
    if percent >= 100:
        tone = "red"
    elif percent >= 80:  # порог предупреждения — держим рядом с plans.WARN_PERCENT
        tone = "yellow"
    width = min(100, max(0, percent))
    return (
        '<div class="usage">'
        f'<div class="usage-track"><div class="usage-fill u-{tone}" '
        f'style="width:{width}%"></div></div>'
        f'<span class="usage-text">{int(used)} из {int(limit)} ({percent}%)</span>'
        "</div>"
    )


def card(title: str, body: str) -> str:
    return f'<div class="card"><h2>{esc(title)}</h2>{body}</div>'


def table(headers: Iterable[str], rows: list[list[str]], empty: str = "Пока пусто") -> str:
    """Пустую таблицу заменяем скелетоном загрузки — выглядит живей, чем текст."""
    if not rows:
        return skeleton("table_row", count=3)
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def skeleton(kind: str = "text", count: int = 1) -> str:
    """Скелетон загрузки: анимированный серый блок вместо пустого состояния.

    kind:
      text      — одна строка текста (строка в карточке)
      title     — заголовок карточки/раздела
      avatar    — квадратная аватарка мастера/клиента
      badge     — бейдж статуса
      tile      — плитка метрики (плитки «Сегодня»)
      table_row — строка таблицы (записи, клиенты, контрагенты)
      grid      — сетка плиток (count плиток)
      list      — список строк таблицы (count строк)
    """
    if kind == "grid":
        tiles = "".join('<div class="skeleton-tile"></div>' for _ in range(count))
        return f'<div class="skeleton-grid">{tiles}</div>'
    if kind == "list":
        rows = "".join('<div class="skeleton-table-row"></div>' for _ in range(count))
        return f'<div class="skeleton-list">{rows}</div>'
    one = {
        "text": '<div class="skeleton-text"></div>',
        "title": '<div class="skeleton-title"></div>',
        "avatar": '<div class="skeleton-avatar"></div>',
        "badge": '<div class="skeleton-badge"></div>',
        "tile": '<div class="skeleton-tile"></div>',
        "table_row": '<div class="skeleton-table-row"></div>',
    }[kind]
    return "".join(one for _ in range(count))


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

    Отправку на ``/salon`` делает внешний ``salon.js`` (по ``change``), а не
    инлайновый ``onchange``: CSP панели — ``script-src 'self'`` без
    ``'unsafe-inline'``, поэтому инлайновый обработчик не выполнялся и выбор
    точки ничего не делал.
    """
    if not salons:
        return ""
    sid = str(salon["id"]) if salon else ""
    opts = "".join(
        f'<option value="{esc(s["id"])}"{" selected" if sid == str(s["id"]) else ""}>'
        f'{esc(s["name"])}</option>'
        for s in salons
    )
    return (
        '<form method="get" action="/salon" class="salon-switch">'
        f'<select name="id" id="salon-select">{opts}</select></form>'
    )


def t(key: str, salon: dict | None, default: str = "") -> str:
    """Термин интерфейса для вертикали тенанта.

    Берется из ``salon["terms"]`` (jsonb). Если ключа нет — ``default``.
    """
    if not salon:
        return default
    terms = salon.get("terms") or {}
    if isinstance(terms, str):
        import json
        try:
            terms = json.loads(terms)
        except Exception:
            terms = {}
    return str(terms.get(key, default))


def module_enabled(path: str, salon: dict | None) -> bool:
    """Раздел ``path`` включён для тенанта?

    Сопоставляет путь с ключом в ``salon["modules"]``:
    /clients -> clients, /services -> services, /masters -> masters,
    /bookings -> bookings, /dialogs -> dialogs, /reports -> reports,
    /pay -> pay, /settings -> settings, /today -> bookings.
    """
    if not salon:
        return True
    modules = salon.get("modules") or {}
    if isinstance(modules, str):
        import json
        try:
            modules = json.loads(modules)
        except Exception:
            modules = {}
    key_map = {
        "/today": "bookings",
        "/bookings": "bookings",
        "/clients": "clients",
        "/services": "services",
        "/masters": "masters",
        "/dialogs": "dialogs",
        "/reports": "reports",
        "/pay": "pay",
        "/settings": "settings",
    }
    key = key_map.get(path)
    if key is None:
        return True
    return bool(modules.get(key, True))


def _theme_css(salon: dict | None) -> str:
    """Инлайн-стили темы тенанта: акцентный цвет и т.д."""
    if not salon:
        return ""
    theme = salon.get("theme") or {}
    if isinstance(theme, str):
        import json
        try:
            theme = json.loads(theme)
        except Exception:
            theme = {}
    accent = theme.get("accent", "#c2410c")
    return (
        f'<style>body{{--accent:{accent};--accent-hover:{accent};'
        f'--accent-bg:rgba(194,65,12,.16)}}</style>'
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
        if module_enabled(item[0], salon)
        if (
            (item[0] in ("/network", "/billing", "/counterparties") and role == "team")
            or (item[0] == "/pay" and role in ("owner", "team"))
            or item[0] not in ("/network", "/billing", "/counterparties", "/pay")
        )
    ]
    nav = "".join(
        f'<a href="{path}?salon={esc(sid)}" class="{"active" if path == active else ""}">'
        f"<i>{icon}</i>{esc(name)}</a>"
        for path, name, icon in nav_items
    )
    switch = salon_switch(salon, salons) if not switch_inside else ""
    theme_style = _theme_css(salon)
    return (
        '<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="dark">'
        '<style>.skip-link{position:absolute;left:-9999px;top:auto;width:1px;height:1px;overflow:hidden}</style>'
        f"<title>{esc(title)} — Дежурный24</title>"
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@500;600;700&display=swap" rel="stylesheet">'
        '<link rel="stylesheet" href="/static.css">'
        f"{theme_style}"
        '</head><body>'
        '<a href="#main" class="skip-link">Перейти к основному содержимому</a>'
        '<div class="shell"><aside><div class="brand">Дежурный<span>24</span></div>'
        f"<nav>{nav}</nav>"
        '<div class="foot">MVP · read-only<br>фаза 4</div></aside>'
        f'<main id="main" class="{"inner" if inner_scroll else ""}">'
        # Контент обязательно в своей обёртке: .top — flex, и без неё все
        # блоки страницы становились его детьми и выстраивались в один ряд
        # (расписание вставало справа от плиток вместо того, чтобы быть под ними).
        f'<div class="top"><div class="content">{content}</div>{switch}</div></main>'
        "</div>"
        # Внешний скрипт (не инлайн — CSP запрещает): дёргает отправку формы
        # переключателя точки по событию change на #salon-select.
        '<script src="/salon.js" defer></script>'
        '<script src="/modal.js" defer></script>'
        "</body></html>"
    )


def login_page(
    error: str = "",
    *,
    login: str = "",
    need_totp: bool = False,
    agreed: bool = False,
    remember: bool = False,
) -> str:
    """Вход в панель: логин + пароль (+ код 2FA, если у пользователя включена).

    Вход загорожен двумя обязательными согласиями — офертой и обработкой ПДн:
    сервер отклоняет POST без них, а кнопка неактивна, пока не отмечены обе
    галочки (CSS :has(), как и в остальной панели — без JavaScript).

    ``agreed=True`` — согласия уже получены в этом же POST (шаг ввода 2FA или
    повтор после ошибки): рендерим их скрытыми полями, чтобы следующий POST
    прошёл серверную проверку без повторных кликов. ``remember`` — состояние
    галочки «Запомнить меня» (долгая сессия + сохранение пароля браузером).
    """
    msg = f'<div class="flash err">{esc(error)}</div>' if error else ""
    totp_field = ""
    if need_totp:
        totp_field = (
            '<label for="code">Код из приложения (6 цифр)</label>'
            '<input id="code" name="code" type="text" inputmode="numeric" '
            'pattern="[0-9]{6}" maxlength="6" autofocus required>'
        )
    remember_checked = " checked" if remember else ""
    if agreed:
        # Согласие уже дано в этом POST — рисуем чекбоксы уже отмеченными,
        # чтобы CSS :has(...) увидел их и разблокировал кнопку «Войти».
        consent = (
            '<div class="agree">'
            '<label class="chk"><input type="checkbox" name="agree_oferta" '
            'checked> <span>Я принимаю <a href=\"/oferta\" target=\"_blank\" '
            'rel=\"noopener\">условия публичной оферты</a></span></label>'
            '<label class="chk"><input type="checkbox" name="agree_pdn" '
            'checked> <span>Я согласен на <a href=\"/pdn\" target=\"_blank\" '
            'rel=\"noopener\">обработку персональных данных</a></span></label>'
            '<label class="chk"><input type="checkbox" name="remember" '
            f'id="remember" value="1"{remember_checked}> '
            "<span>Запомнить меня</span></label>"
            "</div>"
        )
    else:
        consent = (
            '<div class="agree">'
            '<label class="chk"><input type="checkbox" name="agree_oferta" '
            'required> <span>Я принимаю <a href="/oferta" target="_blank" '
            'rel="noopener">условия публичной оферты</a></span></label>'
            '<label class="chk"><input type="checkbox" name="agree_pdn" '
            'required> <span>Я согласен на <a href="/pdn" target="_blank" '
            'rel="noopener">обработку персональных данных</a></span></label>'
            '<label class="chk"><input type="checkbox" name="remember" '
            f'id="remember" value="1"{remember_checked}> '
            "<span>Запомнить меня</span></label>"
            "</div>"
        )
    return (
        '<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="dark">'
        '<style>.skip-link{position:absolute;left:-9999px;top:auto;width:1px;height:1px;overflow:hidden}</style>'
        "<title>Вход — Дежурный24</title>"
        '<link rel="stylesheet" href="/static.css"></head><body>'
        '<a href="#main" class="skip-link">Перейти к основному содержимому</a>'
        '<div class="login" id="main"><form method="post" action="/login">'
        '<div class="brand" style="padding:0 0 6px">Дежурный<span>24</span></div>'
        '<div class="sub">Панель управления точкой</div>'
        f"{msg}"
        '<label for="login">Логин (e-mail)</label>'
        f'<input id="login" name="login" type="email" autocomplete="username" '
        f'value="{esc(login)}" '
        f"required>"
        '<label for="pwd">Пароль</label>'
        # autocomplete="off" по умолчанию: пароль не сохраняется, пока пользователь
        # не поставил «Запомнить меня» (login.js включает сохранение на submit).
        '<input id="pwd" name="password" type="password" autocomplete="off" required>'
        f"{totp_field}"
        f"{consent}"
        '<div style="margin-top:18px"><button type="submit" style="width:100%">Войти</button></div>'
        '<div class="sub" style="margin-top:12px;text-align:center">'
        '<a href="/oferta" target="_blank" rel="noopener">Оферта</a> · '
        '<a href="/pdn" target="_blank" rel="noopener">Обработка ПДн</a>'
        "</div>"
        "</form>"
        '<script src="/login.js" defer></script>'
        "</div></body></html>"
    )
