"""Заголовки безопасности и работа за TLS (DEVELOPMENT_PLAN.md §8.2 — HTTPS + HSTS).

Панель показывает ПДн клиентов, поэтому заголовки ставятся всегда, а HSTS —
только когда соединение действительно защищено. Иначе браузер запомнит
«HTTPS на весь домен» на локальной разработке и сломает http://localhost.
"""

from __future__ import annotations

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


def https_redirect_target(request: Any, *, tls_enabled: bool) -> str | None:
    """Куда перенаправить HTTP-запрос на HTTPS (None — перенаправлять не нужно)."""
    if not tls_enabled or request.secure:
        return None
    host = request.headers.get("Host") or getattr(request, "host", None) or "localhost"
    path = request.raw_path or "/"
    return f"https://{host}{path}"
