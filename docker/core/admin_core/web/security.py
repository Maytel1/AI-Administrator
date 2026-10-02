"""Заголовки безопасности и работа за TLS (DEVELOPMENT_PLAN.md §8.2 — HTTPS + HSTS).

Панель показывает ПДн клиентов, поэтому заголовки ставятся всегда, а HSTS —
только когда соединение действительно защищено. Иначе браузер запомнит
«HTTPS на весь домен» на локальной разработке и сломает http://localhost.
"""

from __future__ import annotations

import re
from typing import Any

HSTS_MAX_AGE = 31_536_000  # 1 год — рекомендация OWASP для административных панелей

# Панель рендерится целиком на сервере и не грузит ничего внешнего:
# скриптов с CDN нет намеренно. Поэтому CSP такой строгий — это не бутафория.
CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "  # инлайновые style= в таблицах
    "img-src 'self' data:; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "object-src 'none'"
)


def is_secure_request(request: Any, *, tls_enabled: bool, trust_proxy: bool) -> bool:
    """Защищено ли соединение: сам TLS либо заголовок обратного прокси."""
    if tls_enabled and request.secure:
        return True
    if trust_proxy:
        proto = request.headers.get("X-Forwarded-Proto", "").lower()
        return proto == "https"
    return False


def security_headers(*, secure: bool) -> dict[str, str]:
    """Набор заголовков для каждого ответа. HSTS — только при secure."""
    headers = {
        "Content-Security-Policy": CSP,
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
        "Cache-Control": "no-store",  # панель показывает ПДн — не кэшируем в браузере
    }
    if secure:
        headers["Strict-Transport-Security"] = f"max-age={HSTS_MAX_AGE}; includeSubDomains"
    return headers


def cookie_secure_flag(*, secure: bool) -> bool:
    """Secure у cookie ставим только под HTTPS — иначе браузер её не примет."""
    return secure


# Куда можно возвращать браузер после POST. Проверка против open redirect:
# "//evil.example" тоже начинается со «/», поэтому одного startswith мало.
SAFE_REDIRECT_PREFIXES = ("/today", "/bookings", "/clients", "/services", "/masters",
                          "/dialogs", "/reports", "/audit", "/settings")

# Допустимые символы имени хоста. Всё остальное (пробелы, «@», «/», обратные
# слэши, управляющие символы) означает подделку Host — такой редирект уводит
# браузер администратора на фишинговый домен.
_HOST_RE = re.compile(r"^[A-Za-z0-9.\-_]+(?::\d{1,5})?$")


def safe_redirect(target: str | None, default: str = "/today") -> str:
    """Внутренний адрес для редиректа или ``default``.

    Отсекаем: абсолютные URL (``http://…``), протокол-относительные (``//evil.host``),
    обратные слэши, управляющие символы и всё, что не начинается с ``/``.
    Иначе злоумышленник подсовывает ``back=//evil.example`` и уводит
    администратора панели на фишинг с настоящего домена.
    """
    candidate = (target or "").strip()
    if not candidate or len(candidate) > 200:
        return default
    if not candidate.startswith("/") or candidate.startswith("//"):
        return default
    if "\\" in candidate or any(ch in candidate for ch in "\r\n\t"):
        return default
    if not candidate.startswith(SAFE_REDIRECT_PREFIXES):
        return default
    return candidate


def https_redirect_target(
    request: Any, *, tls_enabled: bool, allowed_hosts: tuple[str, ...] = ()
) -> str | None:
    """Куда перенаправить HTTP-запрос на HTTPS (None — перенаправлять не нужно).

    Host берётся из заголовка, а он подделывается одной строкой: без проверки
    ``Host: evil.example`` превращает редирект в оружие фишинга. Поэтому:
    1) синтаксически допустимые имена хостов (без «@», пробелов, «/»);
    2) если задан WEB_PUBLIC_HOST — только домены из этого списка.
    """
    if not tls_enabled or request.secure:
        return None
    raw_host = request.headers.get("Host") or getattr(request, "host", "") or "localhost"
    host = str(raw_host).strip()
    if not _HOST_RE.match(host):
        return None  # подозрительный Host: лучше не редиректить, чем увести на фишинг
    if allowed_hosts:
        bare = host.split(":")[0].lower()
        if not any(bare == h.split(":")[0].lower() for h in allowed_hosts):
            host = allowed_hosts[0]
    path = request.raw_path or "/"
    return f"https://{host}{path}"
