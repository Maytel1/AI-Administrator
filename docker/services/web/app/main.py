"""web-service — HTTP-обвязка веб-панели «Дежурный24» (фаза 4).

Логика панели живёт в ``admin_core.web`` (запросы, страницы, сессии), здесь
только транспорт: логин, роуты разделов, статика и health/metrics.

Доступ (DEVELOPMENT_PLAN.md §8.2): пароль ``WEB_PANEL_PASSWORD``, сессия в cookie
с httponly, изоляция выборок по ``salon_id``. В проде — HTTPS + TOTP-2FA (TODO).
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
from admin_core.web import actions, dashboard, queries
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
from admin_core.web.session import SESSION_COOKIE, Panel, check_builder_signatures

STATIC_DIR = Path(__file__).parent


async def _today(panel: Panel, salon: dict, salons: list[dict], request: Any, csrf: str = "") -> str:
    """Дашборд дня: сутки салона по его таймзоне."""
    tz = await queries.salon_tz(panel.pool, str(salon["id"]))
    start, end = queries.day_bounds(tz)
    stats = await dashboard.today_stats(panel.pool, str(salon["id"]), start, end)
    schedule = await dashboard.schedule_for_day(panel.pool, str(salon["id"]), start, end)
    panel.metrics.messages.labels("page_view").inc()
    msg, err = _flash_from(request)
    return page_today(stats, schedule, tz, salon, salons, csrf, msg, err)


def _flash_from(request: Any) -> tuple[str, bool]:
    """Результат прошлой правки перекладываем в query после редиректа."""
    return str(request.query.get("msg", "")), request.query.get("err") == "1"


async def _bookings(
    panel: Panel, salon: dict, salons: list[dict], request: Any, csrf: str = ""
) -> str:
    """Лента записей за ±30 дней вокруг «сейчас»; статус — из query."""
    tz = await queries.salon_tz(panel.pool, str(salon["id"]))
    now = datetime.now(tz)
    start = (now - timedelta(days=30)).astimezone(ZoneInfo("UTC"))
    end = (now + timedelta(days=30)).astimezone(ZoneInfo("UTC"))
    status = request.query.get("status", "")
    rows = await queries.bookings_between(panel.pool, str(salon["id"]), start, end, status)
    msg, err = _flash_from(request)
    return page_bookings(rows, tz, status, salon, salons, csrf, msg, err)


async def _clients(
    panel: Panel, salon: dict, salons: list[dict], request: Any, csrf: str = ""
) -> str:
    tz = await queries.salon_tz(panel.pool, str(salon["id"]))
    rows = await queries.clients_list(panel.pool, str(salon["id"]))
    msg, err = _flash_from(request)
    return page_clients(rows, tz, salon, salons, csrf, msg, err)


async def _services(
    panel: Panel, salon: dict, salons: list[dict], request: Any, csrf: str = ""
) -> str:
    rows = await queries.services_list(panel.pool, str(salon["id"]))
    msg, err = _flash_from(request)
    return page_services(rows, salon, salons, csrf, msg, err)


async def _masters(
    panel: Panel, salon: dict, salons: list[dict], request: Any, csrf: str = ""
) -> str:
    rows = await queries.masters_list(panel.pool, str(salon["id"]))
    msg, err = _flash_from(request)
    return page_masters(rows, salon, salons, csrf, msg, err)


async def _dialogs(
    panel: Panel, salon: dict, salons: list[dict], request: Any, csrf: str = ""
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
    panel: Panel, salon: dict, salons: list[dict], request: Any, csrf: str = ""
) -> str:
    tz = await queries.salon_tz(panel.pool, str(salon["id"]))
    rows = await queries.audit_list(panel.pool, str(salon["id"]))
    msg, err = _flash_from(request)
    return page_audit(rows, tz, salon, salons)


async def _settings(
    panel: Panel, salon: dict, salons: list[dict], request: Any, csrf: str = ""
) -> str:
    env = {"LLM_PROVIDER": os.getenv("LLM_PROVIDER", "—")}
    return page_settings(salon, salons, env)


def build_app(panel: Panel) -> Any:
    """Приложение aiohttp: статика, логин, разделы панели."""
    from aiohttp import web  # noqa: PLC0415 — в cli-контейнере aiohttp нет

    async def page(request: Any, builder: Any) -> Any:
        """Общая обвязка раздела: проверка доступа, выбор салона, HTML-ответ."""
        session = request.cookies.get(SESSION_COOKIE, "")
        if not panel.authorized(session):
            return web.Response(text=login_page(), content_type="text/html")
        try:
            salon, salons = await panel.salon_or_first(request)
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
                panel, salon, salons, request, panel.csrf_token(session)
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

        Проверяем сессию и CSRF, дальше форма уходит в ``admin_core.web.actions``:
        салон берётся из сессии выбора, а не из формы — подменить нельзя.
        """
        session = request.cookies.get(SESSION_COOKIE, "")
        if not panel.authorized(session):
            return web.HTTPFound("/login")
        form = await request.post()
        if not panel.csrf_ok(session, str(form.get("csrf", ""))):
            panel.metrics.errors.labels("csrf").inc()
            return web.HTTPFound("/today?err=1&msg=" + quote(
                "Форма устарела — обновите страницу и попробуйте снова", safe=""
            ))
        salon, _salons = await panel.salon_or_first(request)
        if salon is None:
            return web.HTTPFound("/today?err=1&msg=" + quote("Нет подключённых точек", safe=""))
        result = await actions.apply_form(panel.pool, str(salon["id"]), form)
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
        data = await request.post()
        token = panel.login(str(data.get("password", "")))
        if not token:
            return web.Response(text=login_page("Неверный пароль"), content_type="text/html")
        resp = web.HTTPFound("/today")
        resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="Lax", path="/")
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
    app = web.Application()
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
        print("[web] ВНИМАНИЕ: WEB_PANEL_PASSWORD не задан — вход будет невозможен", flush=True)
    port = int(os.getenv("WEB_HTTP_PORT", "8080"))
    runner = web.AppRunner(build_app(panel))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    health = await start_health_server(
        make_health_app(panel.metrics, pool=pool, provider="panel"), port=port + 1
    )
    print(
        f"[web] панель: http://localhost:{port}/today | health: :{port + 1}/health",
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
