"""Сессии и выбор салона для веб-панели (transport-независимо).

Сессии в памяти процесса: панель перезапускается вместе с деплоем, для демо
и пилота этого достаточно. В проде понадобится общий стор (Redis или PG).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from admin_core.health import ServiceMetrics

from . import queries
from .roles import can
from .users import authenticate

SESSION_TTL = timedelta(hours=12)
# «Запомнить меня»: сессия живёт 30 дней вместо 12 часов; cookie — с max_age.
REMEMBER_TTL = timedelta(days=30)
SESSION_COOKIE = "dz24_session"
# Логин (не пароль!) из «Запомнить меня» — только для предзаполнения поля.
LOGIN_COOKIE = "dz24_login"


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


class Throttle:
    """Счётчик неудачных попыток: логин, IP, код 2FA.

    Без него перебор пароля и шестизначного кода TOTP реалистичен: у кода
    всего миллион комбинаций на 30-секундное окно.

    Ключи приходят извне, поэтому их число ограничено: иначе тысячи запросов
    с разными «логинами» съедают память процесса (отказ в обслуживании).
    """

    MAX_KEYS = 4096

    def __init__(self, limit: int, window: float, *, clock=time.monotonic) -> None:
        self.limit = limit
        self.window = window
        self._clock = clock
        self._fails: dict[str, list[float]] = {}

    def allowed(self, key: str) -> bool:
        """Можно пробовать дальше (окно скользящее)."""
        return len(self._fresh(key)) < self.limit

    def fail(self, key: str) -> None:
        self._fails[key] = self._fresh(key) + [self._clock()]
        if len(self._fails) > self.MAX_KEYS:
            self._prune()

    def reset(self, key: str) -> None:
        self._fails.pop(key, None)

    def remaining(self, key: str) -> int:
        return max(0, self.limit - len(self._fresh(key)))

    def prune(self) -> int:
        """Вычистить протухшее; если свежего всё равно много — срезаем половину.

        Возвращаем число удалённых ключей — это видно в метриках.
        """
        return self._prune()

    def _prune(self) -> int:
        before = len(self._fails)
        for key in list(self._fails):
            self._fresh(key)
        overflow = len(self._fails) - self.MAX_KEYS
        if overflow > 0:
            for key in list(self._fails)[: overflow + self.MAX_KEYS // 2]:
                self._fails.pop(key, None)
        return before - len(self._fails)

    def _fresh(self, key: str) -> list[float]:
        now = self._clock()
        hits = [t for t in self._fails.get(key, ()) if now - t < self.window]
        if hits:
            self._fails[key] = hits
        else:
            self._fails.pop(key, None)
        return hits


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
        # ``password`` оставлен для совместимости вызовов, но НЕ используется:
        # общий пароль давал роль team (доступ ко всем точкам). Учётные записи —
        # единственный способ входа, см. users.authenticate.
        self.password = ""
        self.metrics = metrics or ServiceMetrics("web-service")
        self.sessions: dict[str, Session] = {}
        self.once: dict[str, list[dict[str, Any]]] = {}
        # перебор пароля и кода 2FA: отдельно по логину и по IP-адресу
        self.login_throttle = Throttle(limit=5, window=900.0)
        self.ip_throttle = Throttle(limit=20, window=900.0)
        # серверный секрет для CSRF: токен привязан к сессии, подделать его нельзя
        self.secret = secrets.token_bytes(32)

    def login_blocked(self, login: str, ip: str) -> bool:
        """Заблокирован ли вход: слишком много неудач по этому логину или по IP."""
        return not (
            self.login_throttle.allowed(f"login:{login.lower()}")
            and self.ip_throttle.allowed(f"ip:{ip}")
        )

    def note_login_failure(self, login: str, ip: str) -> None:
        self.login_throttle.fail(f"login:{login.lower()}")
        self.ip_throttle.fail(f"ip:{ip}")
        self.metrics.errors.labels("login_failed").inc()

    def note_login_success(self, login: str, ip: str) -> None:
        self.login_throttle.reset(f"login:{login.lower()}")
        self.ip_throttle.reset(f"ip:{ip}")

    def csrf_token(self, session_token: str) -> str:
        """CSRF-токен формы: HMAC от секрета сервера и токена сессии."""
        return hmac.new(self.secret, session_token.encode(), hashlib.sha256).hexdigest()[:32]

    def csrf_ok(self, session_token: str, submitted: str) -> bool:
        """Сравнение константным временем — иначе токен подбирается по таймингу."""
        if not session_token or not submitted:
            return False
        return hmac.compare_digest(self.csrf_token(session_token), submitted)

    async def login_user(
        self, login: str, password: str, code: str = "", *, remember: bool = False,
    ) -> tuple[str, str]:
        """Вход по учётной записи. Возвращает (токен, ошибка); токен '' — не вошли.

        ``remember=True`` (галочка «Запомнить меня») — сессия живёт REMEMBER_TTL
        (30 дней) вместо обычных 12 часов.
        """
        result = await authenticate(self.pool, login, password, code)
        if not result["ok"]:
            if result["error"] == "need_totp":
                return "", "need_totp"
            self.metrics.errors.labels("login").inc()
            await self._log_login(login, ok=False, reason=result["error"])
            return "", "invalid"
        user = result["user"]
        ttl = REMEMBER_TTL if remember else SESSION_TTL
        token = secrets.token_urlsafe(24)
        self.sessions[token] = Session(
            user=user, role=user["role"], login=user["login"],
            salon_id=user["salon_id"], until=datetime.now(ZoneInfo("UTC")) + ttl,
        )
        # Дублируем в БД: иначе при двух инстансах веб-сервиса вход, сделанный
        # на одном, не работает на другом — и выглядит как «панель выкидывает».
        await self._save_session(token, self.sessions[token])
        self.metrics.messages.labels("login").inc()
        return token, ""

    async def open_session(
        self, *, role: str, login: str, salon_id: str | None,
        user: dict[str, Any] | None = None,
    ) -> str:
        """Выдать сессию минуя проверку пароля.

        Нужна для тестов HTTP-слоя и для аварийного входа на сервере. Вход через
        форму пароль проверяет обязательно — этот метод для тех, у кого есть
        прямой доступ к процессу.

        Сессия обязательно попадает в БД: ``authorized()`` сверяется с
        ``panel_sessions``, иначе «аварийный» вход работал бы только до первого
        же запроса и тихо выкидывал бы администратора.
        """
        token = secrets.token_urlsafe(24)
        session = Session(
            user=user or {"login": login, "role": role}, role=role, login=login,
            salon_id=salon_id, until=datetime.now(ZoneInfo("UTC")) + SESSION_TTL,
        )
        self.sessions[token] = session
        await self._save_session(token, session)
        return token

    async def _save_session(self, token: str, session: Session) -> None:
        if self.pool is None:
            return
        try:
            await self.pool.execute(
                "INSERT INTO panel_sessions (token, user_id, login, role, salon_id, "
                "expires_at) VALUES ($1, $2::uuid, $3, $4, $5::uuid, $6) "
                "ON CONFLICT (token) DO NOTHING",
                token, (session.user or {}).get("id"), session.login, session.role,
                session.salon_id, session.until,
            )
        except Exception:  # noqa: BLE001 — сессия уже в памяти, БД не критичен
            self.metrics.errors.labels("session_store").inc()

    async def _load_session(self, token: str) -> Session | None:
        """Поднять сессию из БД, если её нет в памяти текущего инстанса."""
        if self.pool is None or not token:
            return None
        try:
            row = await self.pool.fetchrow(
                "SELECT user_id, login, role, salon_id, expires_at FROM panel_sessions "
                "WHERE token = $1",
                token,
            )
        except Exception:  # noqa: BLE001 — таблицы может не быть (первый запуск)
            return None
        if not row or datetime.now(ZoneInfo("UTC")) > row["expires_at"]:
            return None
        self.sessions[token] = Session(
            user={"id": str(row["user_id"]) if row["user_id"] else None},
            role=row["role"], login=row["login"] or "",
            salon_id=str(row["salon_id"]) if row["salon_id"] else None,
            until=row["expires_at"],
        )
        return self.sessions[token]

    async def _drop_session(self, token: str) -> None:
        if self.pool is None or not token:
            return
        try:
            await self.pool.execute(
                "DELETE FROM panel_sessions WHERE token = $1", token
            )
        except Exception:  # noqa: BLE001 — выход из системы не должен падать
            self.metrics.errors.labels("session_store").inc()

    async def _log_login(
        self, login: str, *, ok: bool, reason: str = "", ip: str = "", agent: str = ""
    ) -> None:
        """Журнал входов в БД: переживает рестарт, нужен для разбора инцидента."""
        if self.pool is None:
            return
        from ..monitoring import log_login  # noqa: PLC0415 — ленивый импорт

        try:
            await log_login(self.pool, login=login, ip=ip, ok=ok, reason=reason,
                            user_agent=agent)
        except Exception as exc:  # noqa: BLE001 — журнал не должен ломать вход
            # Ошибку печатаем явно: иначе «журнала нет» тихо выглядит как
            # «никто не входил» — а это разные вещи при разборе инцидента.
            self.metrics.errors.labels("login_log").inc()
            print(f"[web] журнал входов не записан: {type(exc).__name__}: {exc}",
                  flush=True)

    async def authorized(self, token: str) -> bool:
        """Проверка cookie; просроченные и чужие токены попутно вычищаем.

        Сессия может прийти из БД: при нескольких инстансах веб-сервиса вход,
        сделанный на одном, обязан работать на другом.
        """
        session = self.sessions.get(token or "")
        if session is None:
            session = await self._load_session(token)
        if session is None:
            return False
        if datetime.now(ZoneInfo("UTC")) > session.until:
            self.sessions.pop(token, None)
            await self._drop_session(token)
            return False
        # Сессию могли завершить на другом инстансе: без сверки с БД «выход»
        # действовал бы только в том процессе, где его нажали.
        if await self._revoked(token):
            self.sessions.pop(token, None)
            return False
        return True

    async def _revoked(self, token: str) -> bool:
        """Есть ли токен в БД. Несовпадение = сессии больше нет."""
        if self.pool is None:
            return False
        try:
            row = await self.pool.fetchrow(
                "SELECT 1 FROM panel_sessions WHERE token = $1", token
            )
        except Exception:  # noqa: BLE001 — таблицы может не быть
            return False
        return row is None

    async def session_of(self, token: str) -> Session | None:
        session = self.sessions.get(token)
        if session is None:
            session = await self._load_session(token)
        return session

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

    def purge_expired_sessions(self) -> int:
        """Вычистить истёкшие сессии и счётчики перебора.

        Без этого словари растут всю жизнь процесса: сессия попадает в память
        на каждый вход, а ключи троттла — на каждую неудачную попытку.
        """
        self._purge_expired_once()
        self.login_throttle.prune()
        self.ip_throttle.prune()
        return len(self.sessions)

    async def purge_expired_sessions_db(self) -> int:
        """Вычистить протухшие сессии в БД — для нескольких инстансов веба.

        Вызывается из фонового таймера сервиса, где await доступен.
        """
        self._purge_expired_once()
        if self.pool is not None:
            try:
                await self.pool.execute(
                    "DELETE FROM panel_sessions WHERE expires_at < now()"
                )
            except Exception:  # noqa: BLE001 — чистка не должна ломать сервис
                self.metrics.errors.labels("session_store").inc()
        self.login_throttle.prune()
        self.ip_throttle.prune()
        return len(self.sessions)

    def _purge_expired_once(self) -> None:
        """Только память процесса — вызывается из синхронного таймера."""
        now = datetime.now(ZoneInfo("UTC"))
        for token, session in list(self.sessions.items()):
            if now > session.until:
                self.sessions.pop(token, None)

    def role_of(self, token: str) -> str:
        """Роль текущей сессии; без сессии — гость (ни одного права)."""
        session = self.sessions.get(token or "")
        return session.role if session else "guest"

    async def logout(self, token: str) -> None:
        self.sessions.pop(token or "", None)
        await self._drop_session(token)  # иначе «выход» не работает на другом инстансе

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
