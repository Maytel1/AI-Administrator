"""Сессии и выбор салона для веб-панели (transport-независимо).

Сессии в памяти процесса: панель перезапускается вместе с деплоем, для демо
и пилота этого достаточно. В проде понадобится общий стор (Redis или PG).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from admin_core.health import ServiceMetrics

from . import queries

SESSION_TTL = timedelta(hours=12)
SESSION_COOKIE = "dz24_session"


class Panel:
    """Состояние панели: пул БД, метрики, таблица сессий."""

    def __init__(self, pool: Any, password: str, *, metrics: ServiceMetrics | None = None) -> None:
        self.pool = pool
        self.password = password
        self.metrics = metrics or ServiceMetrics("web-service")
        self.sessions: dict[str, datetime] = {}
        # серверный секрет для CSRF: токен привязан к сессии, подделать его нельзя
        self.secret = secrets.token_bytes(32)

    def csrf_token(self, session_token: str) -> str:
        """CSRF-токен формы: HMAC от секрета сервера и токена сессии."""
        return hmac.new(self.secret, session_token.encode(), hashlib.sha256).hexdigest()[:32]

    def csrf_ok(self, session_token: str, submitted: str) -> bool:
        """Сравнение константным временем — иначе токен подбирается по таймингу."""
        if not session_token or not submitted:
            return False
        return hmac.compare_digest(self.csrf_token(session_token), submitted)

    def login(self, password: str) -> str | None:
        """Сверяем пароль константным временем; при успехе — токен сессии."""
        if not self.password or not hmac.compare_digest(password, self.password):
            self.metrics.errors.labels("login").inc()
            return None
        token = secrets.token_urlsafe(24)
        self.sessions[token] = datetime.now(ZoneInfo("UTC")) + SESSION_TTL
        return token

    def authorized(self, token: str) -> bool:
        """Проверка cookie; просроченные и чужие токены попутно вычищаем."""
        if not token:
            return False
        until = self.sessions.get(token)
        if until is None:
            return False
        if datetime.now(ZoneInfo("UTC")) > until:
            self.sessions.pop(token, None)
            return False
        return True

    def logout(self, token: str) -> None:
        self.sessions.pop(token or "", None)

    async def salons(self) -> list[dict[str, Any]]:
        return await queries.list_salons(self.pool)

    async def salon_or_first(self, request: Any) -> tuple[dict | None, list[dict]]:
        """Тенант из ``?salon=`` или из cookie; иначе первый активный салон."""
        salons = await self.salons()
        wanted = request.query.get("salon") or request.cookies.get("salon")
        found = next((s for s in salons if str(s["id"]) == str(wanted)), None)
        return found or (salons[0] if salons else None), salons


def check_builder_signatures(builders: dict[str, Any]) -> None:
    """Проверка подписей разделов панели на старте сервиса.

    Все builders обязаны принимать ``(panel, salon, salons, request, csrf)``.
    Ловим рассинхрон сразу при старте, а не заглушкой «Не удалось загрузить
    страницу» в HTTP — по заглушке легко пройти мимо проблемы.
    """
    import inspect  # noqa: PLC0415 — нужен только для проверки подписей

    expected = 5  # (panel, salon, salons, request, csrf)
    for name, builder in builders.items():
        params = inspect.signature(builder).parameters
        if len(params) != expected or any(
            p.kind is inspect.Parameter.VAR_POSITIONAL for p in params.values()
        ):
            raise TypeError(
                f"раздел панели «{name}»: ожидаем {expected} параметров "
                f"(panel, salon, salons, request, csrf), а в функции {list(params)}"
            )
