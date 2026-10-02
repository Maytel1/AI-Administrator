"""Сессии и выбор салона для веб-панели (transport-независимо).

Сессии в памяти процесса: панель перезапускается вместе с деплоем, для демо
и пилота этого достаточно. В проде понадобится общий стор (Redis или PG).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from admin_core.health import ServiceMetrics

from . import queries
from .roles import can
from .users import authenticate

SESSION_TTL = timedelta(hours=12)
SESSION_COOKIE = "dz24_session"


@dataclass
class PageCtx:
    """Что странице нужно знать о текущем посетителе.

    Роль — чтобы прятать недоступные действия из интерфейса (сервер всё равно
    проверяет права повторно), csrf — чтобы формы проходили проверку.
    """

    csrf: str
    role: str
    login: str = ""

    def may(self, permission: str) -> bool:
        """Показать действие в UI только если роль действительно может им воспользоваться."""
        return can(self.role, permission)


@dataclass
class Session:
    """Одна сессия панели: кто вошёл, с какой ролью и до какого момента."""

    user: dict[str, Any] | None
    role: str
    login: str
    salon_id: str | None
    until: datetime

    @property
    def can_see_all_salons(self) -> bool:
        """Команда видит все точки; владелец — только свою."""
        return self.role == "team"


class Panel:
    """Состояние панели: пул БД, метрики, таблица сессий.

    Сессия хранит не только токен, но и пользователя с ролью — права проверяются
    на каждом действии, а не только на входе (роль может измениться).
    """

    def __init__(self, pool: Any, password: str = "", *, metrics: ServiceMetrics | None = None) -> None:
        self.pool = pool
        self.password = password  # запасной вход для демо, если нет учёток
        self.metrics = metrics or ServiceMetrics("web-service")
        self.sessions: dict[str, Session] = {}
        self.once: dict[str, list[dict[str, Any]]] = {}
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

    async def login_user(self, login: str, password: str, code: str = "") -> tuple[str, str]:
        """Вход по учётной записи. Возвращает (токен, ошибка); токен '' — не вошли."""
        result = await authenticate(self.pool, login, password, code)
        if not result["ok"]:
            if result["error"] == "need_totp":
                return "", "need_totp"
            self.metrics.errors.labels("login").inc()
            return "", "invalid"
        user = result["user"]
        token = secrets.token_urlsafe(24)
        self.sessions[token] = Session(
            user=user, role=user["role"], login=user["login"],
            salon_id=user["salon_id"], until=datetime.now(ZoneInfo("UTC")) + SESSION_TTL,
        )
        self.metrics.messages.labels("login").inc()
        return token, ""

    def login(self, password: str) -> str | None:
        """Аварийный вход по общему паролю из env — только когда учёток нет."""
        if not self.password or not hmac.compare_digest(password, self.password):
            self.metrics.errors.labels("login").inc()
            return None
        token = secrets.token_urlsafe(24)
        self.sessions[token] = Session(
            user=None, role="team", login="env",
            salon_id=None, until=datetime.now(ZoneInfo("UTC")) + SESSION_TTL,
        )
        return token

    def authorized(self, token: str) -> bool:
        """Проверка cookie; просроченные и чужие токены попутно вычищаем."""
        session = self.sessions.get(token or "")
        if session is None:
            return False
        if datetime.now(ZoneInfo("UTC")) > session.until:
            self.sessions.pop(token, None)
            return False
        return True

    def session_of(self, token: str) -> Session | None:
        return self.sessions.get(token or "")

    def push_once(self, token: str, payload: dict[str, Any]) -> None:
        """Кладём одноразовые данные (пароль, секрет 2FA) в память сессии.

        В URL их класть нельзя: адрес попадает в историю браузера и логи
        прокси. Держим 10 минут и отдаём при следующем показе настроек.
        """
        self.once.setdefault(token, []).append(
            {"at": datetime.now(ZoneInfo("UTC")), "data": payload}
        )

    def take_once(self, token: str) -> list[dict[str, Any]]:
        """Забрать и сразу забыть: второй раз эти данные не показываются."""
        items = self.once.get(token or "", [])
        self.once[token] = []
        return [i["data"] for i in items]

    def purge_expired_once(self) -> None:
        """Подчищаем протухшие одноразовые данные (таймер в фоне)."""
        now = datetime.now(ZoneInfo("UTC"))
        for token, items in list(self.once.items()):
            fresh = [i for i in items if now - i["at"] < timedelta(minutes=10)]
            if fresh:
                self.once[token] = fresh
            else:
                self.once.pop(token, None)

    def role_of(self, token: str) -> str:
        """Роль текущей сессии; без сессии — гость (ни одного права)."""
        session = self.sessions.get(token or "")
        return session.role if session else "guest"

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

    async def salon_for_session(
        self, request: Any, session: Session
    ) -> tuple[dict | None, list[dict]]:
        """Выбор салона с учётом роли.

        Команда видит все точки и может переключаться; владелец и администратор
        ограничены своей точкой — даже если подставить чужой ``?salon=``, вернётся
        его салон, а не чужой (изоляция по tenant_id).
        """
        if not session.can_see_all_salons:
            mine = session.salon_id
            row = await self.pool.fetchrow(
                "SELECT id, name, plan, crm_provider, timezone FROM salons "
                "WHERE id = $1 AND is_active",
                mine,
            )
            own = dict(row) if row else None
            return own, [own] if own else []
        return await self.salon_or_first(request)


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
