"""web-service — HTTP-обвязка веб-панели «Дежурный24» (фаза 4).

Логика панели живёт в ``admin_core.web`` (запросы, страницы, сессии, роли),
здесь только транспорт: логин, роуты разделов, статика и health/metrics.

Доступ (DEVELOPMENT_PLAN.md §8.2): учётные записи с ролями (owner/staff/team),
опциональный TOTP, CSRF-токен на форме, изоляция выборок по ``salon_id``.
Права проверяются дважды: недоступное прячется в UI и отбивается на сервере.
"""

import os
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from admin_core.db import get_pool, run_migrations
from admin_core.health import ServiceMetrics, make_health_app, start_health_server
from admin_core.web import actions, dashboard, queries, users
from admin_core.web.pages_core import page_bookings, page_today
from admin_core.web.pages_lists import (
    page_audit,
    page_clients,
    page_dialogs,
    page_masters,
    page_services,
    page_settings,
)
from admin_core.web.render import layout, login_page
from admin_core.web.roles import audit_actor, can, can_action, role_label
from admin_core.web.security import (
    cookie_secure_flag,
    https_redirect_target,
    is_secure_request,
    security_headers,
)
from admin_core.web.session import (
    SESSION_COOKIE,
    PageCtx,
    Panel,
    check_builder_signatures,
)

STATIC_DIR = Path(__file__).parent


async def _today(
    panel: Panel, salon: dict, salons: list[dict], request: Any, ctx: PageCtx | None = None
) -> str:
    """Дашборд дня: сутки салона по его таймзоне."""
    tz = await queries.salon_tz(panel.pool, str(salon["id"]))
    start, end = queries.day_bounds(tz)
    stats = await dashboard.today_stats(panel.pool, str(salon["id"]), start, end)
    schedule = await dashboard.schedule_for_day(panel.pool, str(salon["id"]), start, end)
    panel.metrics.messages.labels("page_view").inc()
    msg, err = _flash_from(request)
    return page_today(stats, schedule, tz, salon, salons, ctx, msg, err)


def _flash_from(request: Any) -> tuple[str, bool]:
    """Результат прошлой правки перекладываем в query после редиректа."""
    return str(request.query.get("msg", "")), request.query.get("err") == "1"


async def _bookings(
    panel: Panel, salon: dict, salons: list[dict], request: Any, ctx: PageCtx | None = None
) -> str:
    """Лента записей за ±30 дней вокруг «сейчас»; статус — из query."""
    tz = await queries.salon_tz(panel.pool, str(salon["id"]))
    now = datetime.now(tz)
    start = (now - timedelta(days=30)).astimezone(ZoneInfo("UTC"))
    end = (now + timedelta(days=30)).astimezone(ZoneInfo("UTC"))
    status = request.query.get("status", "")
    rows = await queries.bookings_between(panel.pool, str(salon["id"]), start, end, status)
    msg, err = _flash_from(request)
    return page_bookings(rows, tz, status, salon, salons, ctx, msg, err)


async def _clients(
    panel: Panel, salon: dict, salons: list[dict], request: Any, ctx: PageCtx | None = None
) -> str:
    tz = await queries.salon_tz(panel.pool, str(salon["id"]))
    rows = await queries.clients_list(panel.pool, str(salon["id"]))
    msg, err = _flash_from(request)
    return page_clients(rows, tz, salon, salons, ctx, msg, err)


async def _services(
    panel: Panel, salon: dict, salons: list[dict], request: Any, ctx: PageCtx | None = None
) -> str:
    rows = await queries.services_list(panel.pool, str(salon["id"]))
    msg, err = _flash_from(request)
    return page_services(rows, salon, salons, ctx, msg, err)


async def _masters(
    panel: Panel, salon: dict, salons: list[dict], request: Any, ctx: PageCtx | None = None
) -> str:
    rows = await queries.masters_list(panel.pool, str(salon["id"]))
    msg, err = _flash_from(request)
    return page_masters(rows, salon, salons, ctx, msg, err)


async def _dialogs(
    panel: Panel, salon: dict, salons: list[dict], request: Any, ctx: PageCtx | None = None
) -> str:
    """Лента диалогов; ``?id=<dialog_id>`` — раскрыть переписку."""
    tz = await queries.salon_tz(panel.pool, str(salon["id"]))
    rows = await queries.dialogs_list(panel.pool, str(salon["id"]))
    messages = None
    dialog_id = request.query.get("id", "")
    if dialog_id:
        messages = await queries.dialog_messages(panel.pool, dialog_id)
    return page_dialogs(rows, messages, tz, salon, salons)


async def _audit(
    panel: Panel, salon: dict, salons: list[dict], request: Any, ctx: PageCtx | None = None
) -> str:
    tz = await queries.salon_tz(panel.pool, str(salon["id"]))
    rows = await queries.audit_list(panel.pool, str(salon["id"]))
    msg, err = _flash_from(request)
    return page_audit(rows, tz, salon, salons)


async def _settings(
    panel: Panel, salon: dict, salons: list[dict], request: Any, ctx: PageCtx | None = None
) -> str:
    """Настройки: показываем, кто вошёл, его права и команду точки."""
    env = {"LLM_PROVIDER": os.getenv("LLM_PROVIDER", "—")}
    session = panel.session_of(request.cookies.get(SESSION_COOKIE, ""))
    if session is None:
        raise RuntimeError("сессия потеряна между проверкой и обработкой")
    who = {"login": session.login, "role": session.role}
    people = None
    if can(session.role, "users"):
        people = await users.list_users(panel.pool, str(salon["id"]))
    once = panel.take_once(request.cookies.get(SESSION_COOKIE, ""))
    return page_settings(salon, salons, env, who, people, ctx, once)


def build_app(panel: Panel, *, tls_enabled: bool = False, trust_proxy: bool = False) -> Any:
    """Приложение aiohttp: статика, логин, разделы панели."""
    from aiohttp import web  # noqa: PLC0415 — в cli-контейнере aiohttp нет

    @web.middleware
    async def security(request: Any, handler: Any) -> Any:
        """Заголовки безопасности на каждый ответ и редирект HTTP → HTTPS."""
        target = https_redirect_target(request, tls_enabled=tls_enabled)
        if target:
            resp: Any = web.HTTPFound(target, status=301)
        else:
            resp = await handler(request)
        secure = is_secure_request(
            request, tls_enabled=tls_enabled, trust_proxy=trust_proxy
        )
        for name, value in security_headers(secure=secure).items():
            resp.headers[name] = value
        return resp

    async def page(request: Any, builder: Any) -> Any:
        """Общая обвязка раздела: проверка доступа, выбор салона, HTML-ответ."""
        session_token = request.cookies.get(SESSION_COOKIE, "")
        if not panel.authorized(session_token):
            return web.Response(text=login_page(), content_type="text/html")
        session = panel.session_of(session_token)
        try:
            salon, salons = await panel.salon_for_session(request, session)
            if salon is None:
                return web.Response(
                    text=layout(
                        "Пусто", "/today",
                        "<h1>Нет подключённых точек</h1>"
                        '<p class="sub">Загрузите демо-данные: '
                        "docker compose run --rm cli python cli.py --seed</p>",
                    ),
                    content_type="text/html",
                )
            html = await builder(
                panel, salon, salons, request,
                PageCtx(
                    csrf=panel.csrf_token(session_token),
                    role=session.role if session else "guest",
                    login=session.login if session else "",
                ),
            )
        except Exception:  # noqa: BLE001 — панель показывает заглушку, а не падает
            panel.metrics.errors.labels("page").inc()
            traceback.print_exc()  # в лог сервиса: заглушка не должна прятать причину
            html = layout(
                "Ошибка", "/today",
                "<h1>Не удалось загрузить страницу</h1>"
                '<p class="sub">Проверьте БД и попробуйте ещё раз.</p>',
            )
        return web.Response(text=html, content_type="text/html")

    async def do_action(request: Any) -> Any:
        """POST /actions — единственная точка правки данных из панели.

        Порядок проверок: сессия → CSRF → роль на конкретное действие.
        Салон берётся из сессии, а не из формы: подменить нельзя.
        """
        session_token = request.cookies.get(SESSION_COOKIE, "")
        session = panel.session_of(session_token)
        if not panel.authorized(session_token):
            return web.HTTPFound("/login")
        form = await request.post()
        if not panel.csrf_ok(session_token, str(form.get("csrf", ""))):
            panel.metrics.errors.labels("csrf").inc()
            return web.HTTPFound(
                "/today?err=1&msg=" + quote("Форма устарела — обновите страницу", safe="")
            )
        role = session.role if session else "guest"
        action = str(form.get("action", ""))
        if not can_action(role, action):
            panel.metrics.errors.labels("forbidden").inc()
            return web.HTTPFound(
                "/today?err=1&msg=" + quote(
                    f"Роли «{role_label(role)}» не хватает прав на это действие", safe=""
                )
            )
        if session is None:
            return web.HTTPFound("/login")
        salon, _salons = await panel.salon_for_session(request, session)
        if salon is None:
            return web.HTTPFound("/today?err=1&msg=" + quote("Нет подключённых точек", safe=""))
        actor = audit_actor(role, session.login)
        result = await actions.apply_form(
            panel.pool, str(salon["id"]), form, actor=actor
        )
        once = result.get("once")
        if once:
            # пароль и секрет 2FA не идут в URL — покажем на следующей странице
            panel.push_once(session_token, once)
        back = str(form.get("back", "/today")) or "/today"
        if back.startswith("/") and "salon=" not in back:
            back = back + ("&" if "?" in back else "?") + f"salon={salon['id']}"
        params = {"msg": result["message"]}
        if not result["ok"]:
            params["err"] = "1"
            panel.metrics.errors.labels("action").inc()
        sep = "&" if "?" in back else "?"
        return web.HTTPFound(f"{back}{sep}" + urlencode(params))

    async def index(_request: Any) -> Any:
        return web.HTTPFound("/today")

    async def login_get(_request: Any) -> Any:
        return web.Response(text=login_page(), content_type="text/html")

    async def login_post(request: Any) -> Any:
        form = await request.post()
        login = str(form.get("login", "")).strip()
        password = str(form.get("password", ""))
        code = str(form.get("code", ""))
        token, error = await panel.login_user(login, password, code)
        if error == "need_totp":
            return web.Response(
                text=login_page(
                    "Введите код из приложения-аутентификатора",
                    login=login, need_totp=True,
                ),
                content_type="text/html",
            )
        if not token:
            return web.Response(
                text=login_page("Неверный логин или пароль", login=login),
                content_type="text/html",
            )
        resp = web.HTTPFound("/today")
        secure_cookie = cookie_secure_flag(
            secure=is_secure_request(request, tls_enabled=tls_enabled, trust_proxy=trust_proxy)
        )
        resp.set_cookie(
            SESSION_COOKIE, token, httponly=True, samesite="Lax", path="/",
            secure=secure_cookie,
        )
        return resp

    async def logout(request: Any) -> Any:
        panel.logout(request.cookies.get(SESSION_COOKIE, ""))
        resp = web.HTTPFound("/login")
        resp.del_cookie(SESSION_COOKIE, path="/")
        return resp

    async def set_salon(request: Any) -> Any:
        """Переключение тенанта командой админки; запоминаем в cookie."""
        data = await request.get()
        resp = web.HTTPFound("/today")
        if data.get("id"):
            resp.set_cookie("salon", str(data["id"]), httponly=True, samesite="Lax", path="/")
        return resp

    async def static_css(_request: Any) -> Any:
        """Стили отдаём сами — панель работает без внешнего CDN."""
        return web.Response(
            text=(STATIC_DIR / "static.css").read_text(encoding="utf-8"),
            content_type="text/css",
        )

    sections = {
        "/today": _today, "/bookings": _bookings, "/clients": _clients,
        "/services": _services, "/masters": _masters, "/dialogs": _dialogs,
        "/audit": _audit, "/settings": _settings,
    }
    check_builder_signatures(sections)  # падаем на старте, а не в HTTP
    app = web.Application(middlewares=[security])
    app.router.add_get("/", index)
    app.router.add_get("/login", login_get)
    app.router.add_post("/login", login_post)
    app.router.add_get("/logout", logout)
    app.router.add_get("/salon", set_salon)
    app.router.add_post("/actions", do_action)
    app.router.add_get("/static.css", static_css)
    for path, builder in sections.items():
        app.router.add_get(path, lambda r, b=builder: page(r, b))
    return app
async def main() -> None:
    """Старт сервиса: дождаться БД, накатить миграции, поднять панель и health."""
    import asyncio  # noqa: PLC0415

    from aiohttp import web  # noqa: PLC0415

    dsn = os.getenv("DATABASE_URL", "postgresql://aiadmin:aiadmin_dev@postgres:5432/aiadmin")
    pool = None
    for attempt in range(1, 31):
        try:
            pool = await get_pool(dsn)
            break
        except Exception:  # noqa: BLE001 — Postgres ещё поднимается, это норма
            print(f"[web] ждём PostgreSQL ({attempt}/30)", flush=True)
            await asyncio.sleep(2)
    if pool is None:
        print("[web] PostgreSQL так и не поднялся — выход", flush=True)
        raise SystemExit(1)
    await run_migrations(pool)
    panel = Panel(pool, os.getenv("WEB_PANEL_PASSWORD", ""), metrics=ServiceMetrics("web-service"))
    if not panel.password:
        print("[web] ВНИМАНИЕ: WEB_PANEL_PASSWORD не задан — вход закрыт", flush=True)

    tls_cert, tls_key = os.getenv("WEB_TLS_CERT"), os.getenv("WEB_TLS_KEY")
    tls_enabled = bool(tls_cert and tls_key)
    trust_proxy = os.getenv("WEB_TRUST_PROXY", "") == "1"
    if not trust_proxy:
        print("[web] WEB_TRUST_PROXY выключен: X-Forwarded-Proto игнорируется", flush=True)

    port = int(os.getenv("WEB_HTTP_PORT", "8080"))
    ssl_ctx = None
    if tls_enabled:
        import ssl  # noqa: PLC0415 — только когда реально включён TLS

        ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(tls_cert or "", tls_key or "")
        print(f"[web] TLS включён, сертификат: {tls_cert}", flush=True)

    runner = web.AppRunner(
        build_app(panel, tls_enabled=tls_enabled, trust_proxy=trust_proxy)
    )
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port, ssl_context=ssl_ctx).start()
    health = await start_health_server(
        make_health_app(panel.metrics, pool=pool, provider="panel"), port=port + 1
    )
    scheme = "https" if tls_enabled else "http"
    print(
        f"[web] панель: {scheme}://localhost:{port}/today | health: :{port + 1}/health",
        flush=True,
    )
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await health.cleanup()
        await runner.cleanup()
        await pool.close()


if __name__ == "__main__":
    import asyncio  # noqa: PLC0415

    asyncio.run(main())
