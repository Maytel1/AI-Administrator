"""web-service — HTTP-обвязка веб-панели «Дежурный24» (фаза 4).

Логика панели живёт в ``admin_core.web`` (запросы, страницы, сессии, роли),
здесь только транспорт: логин, роуты разделов, статика и health/metrics.

Доступ (DEVELOPMENT_PLAN.md §8.2): учётные записи с ролями (owner/staff/team),
опциональный TOTP, CSRF-токен на форме, изоляция выборок по ``salon_id``.
Права проверяются дважды: недоступное прячется в UI и отбивается на сервере.
"""

import asyncio
import os
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from admin_core.bot_quality import bot_quality
from admin_core.db import get_pool, run_migrations
from admin_core.health import ServiceMetrics, make_health_app, start_health_server
from admin_core.web import actions, dashboard, queries, reports, users
from admin_core.web.pages_core import page_bookings, page_today
from admin_core.web.pages_lists import (
    page_audit,
    page_billing,
    page_clients,
    page_dialogs,
    page_masters,
    page_network,
    page_reports,
    page_services,
    page_settings,
)
from admin_core.web.render import layout, login_page
from admin_core.web.roles import audit_actor, can, can_action, role_label
from admin_core.web.security import (
    cookie_secure_flag,
    https_redirect_target,
    is_secure_request,
    safe_redirect,
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
    """Клиенты точки + лист ожидания: кому предложим освободившийся слот."""
    from admin_core.waitlist import list_waitlist  # noqa: PLC0415

    tz = await queries.salon_tz(panel.pool, str(salon["id"]))
    rows = await queries.clients_list(panel.pool, str(salon["id"]))
    queue = await list_waitlist(panel.pool, str(salon["id"]))
    services = await queries.services(panel.pool, str(salon["id"]))
    msg, err = _flash_from(request)
    html = page_clients(rows, tz, salon, salons, ctx, msg, err)
    return html.replace("<!--WAITLIST-->", _waitlist_block(queue, services, ctx, salon))


def _waitlist_block(
    queue: list[dict[str, Any]],
    services: list[dict[str, Any]],
    ctx: PageCtx | None,
    salon: dict[str, Any] | None,
) -> str:
    """Карточка «Лист ожидания»: очередь и форма добавления клиента."""
    from admin_core.web.pages_lists import waitlist_block  # noqa: PLC0415

    return waitlist_block(queue, services, ctx, salon)


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
    return page_dialogs(rows, messages, tz, salon, salons, ctx)


async def _audit(
    panel: Panel, salon: dict, salons: list[dict], request: Any, ctx: PageCtx | None = None
) -> str:
    tz = await queries.salon_tz(panel.pool, str(salon["id"]))
    rows = await queries.audit_list(panel.pool, str(salon["id"]))
    msg, err = _flash_from(request)
    return page_audit(rows, tz, salon, salons, ctx)


async def _reports(
    panel: Panel, salon: dict, salons: list[dict], request: Any, ctx: PageCtx | None = None
) -> str:
    """Отчёт за выбранный период + сравнение с предыдущим таким же.

    Период приходит из ``?period=`` (7/30/90/месяц). Про сравнение владелец
    просит прямо: одно число выручки ничего не значит без «а сколько было».
    """
    tz = await queries.salon_tz(panel.pool, str(salon["id"]))
    current, previous = reports.resolve_period(request.query.get("period"), tz)
    salon_id = str(salon["id"])
    data = {
        "period_key": current.key,
        "periods": list(reports.PERIODS),
        "window": current,
        "previous_window": previous,
        "by_day": await reports.revenue_by_day(panel.pool, salon_id, current.start, current.end),
        "load": await reports.load_by_master(panel.pool, salon_id, current.start, current.end),
    }
    data.update(await reports.compare_periods(panel.pool, salon_id, current, previous))
    data["funnel"] = data["current"]["funnel"]
    data["loss"] = data["current"]["loss"]
    quality, previous_quality = await asyncio.gather(
        bot_quality(panel.pool, salon_id, current.start, current.end),
        bot_quality(panel.pool, salon_id, previous.start, previous.end),
    )
    data["bot"] = quality
    data["bot_previous"] = previous_quality
    msg, err = _flash_from(request)
    return page_reports(data, salon, salons, msg, err, ctx)


async def _billing(
    panel: Panel, salon: dict, salons: list[dict], request: Any, ctx: PageCtx | None = None
) -> str:
    """Тариф и оплата: состояние подписки, счёт, реквизиты и история платежей."""
    from admin_core import billing  # noqa: PLC0415

    role = ctx.role if ctx else "guest"
    # Реквизиты и платежи — не клиентские данные салона: только «наша команда».
    # Проверка здесь, а не только в меню: пункт скрыт, а URL открыт.
    if not can(role, "billing"):
        panel.metrics.errors.labels("forbidden").inc()
        # Аргументы по именам: позиционно текст отказа уезжал в карточку
        # плательщика, и человек видел пустую страницу без объяснения.
        return page_billing(
            {"status_text": "Недостаточно прав: раздел для нашей команды",
             "price": 0, "plan": "", "requisites": {},
             "is_trial": False, "expired": False},
            salon, salons, [],
            flash="Недостаточно прав на раздел «Тариф и оплата»",
            flash_error=True, ctx=ctx,
        )

    salon_row = dict(await panel.pool.fetchrow(
        "SELECT id, name, plan, paid_until, timezone FROM salons WHERE id = $1",
        salon["id"],
    ))
    status = await billing.billing_status(panel.pool, salon_row)
    history = await panel.pool.fetch(
        "SELECT period_start, period_end, amount, status, paid_at, invoice_no "
        "FROM payments WHERE salon_id = $1 ORDER BY period_start DESC LIMIT 24",
        salon["id"],
    )
    msg, err = _flash_from(request)
    return page_billing(status, salon, salons, history, msg, err, ctx)


async def _network(
    panel: Panel, salon: dict, salons: list[dict], request: Any, ctx: PageCtx | None = None
) -> str:
    """Сводка по всем точкам — только для роли «наша команда».

    Владельцу точки чужие цифры показывать нельзя даже при подмене URL:
    проверка идёт по ``all_salons``, а не по тому, что пришло в ссылке.
    """
    from admin_core.exports import network_summary  # noqa: PLC0415

    role = ctx.role if ctx else "guest"
    if not can(role, "all_salons"):
        panel.metrics.errors.labels("forbidden").inc()
        return page_network([], {}, salon, salons, "Недостаточно прав на сводку", True)
    days = 30
    rows = await network_summary(panel.pool, days=days)
    totals = {
        "salons": len(rows),
        "revenue": sum(float(r["revenue"] or 0) for r in rows),
        "bookings": sum(int(r["bookings"] or 0) for r in rows),
        "no_shows": sum(int(r["no_shows"] or 0) for r in rows),
        "clients": sum(int(r["clients"] or 0) for r in rows),
    }
    msg, err = _flash_from(request)
    return page_network(rows, totals, salon, salons, msg, err, days, ctx)


async def _settings(
    panel: Panel, salon: dict, salons: list[dict], request: Any, ctx: PageCtx | None = None
) -> str:
    """Настройки: показываем, кто вошёл, его права и команду точки."""
    env = {"LLM_PROVIDER": os.getenv("LLM_PROVIDER", "—")}
    session = await panel.session_of(request.cookies.get(SESSION_COOKIE, ""))
    if session is None:
        raise RuntimeError("сессия потеряна между проверкой и обработкой")
    who = {"login": session.login, "role": session.role}
    people = None
    if can(session.role, "users"):
        people = await users.list_users(panel.pool, str(salon["id"]))
    once = panel.take_once(request.cookies.get(SESSION_COOKIE, ""))
    return page_settings(salon, salons, env, who, people, ctx, once)


def build_app(
    panel: Panel, *, tls_enabled: bool = False, trust_proxy: bool = False,
    allowed_hosts: tuple[str, ...] = (),
) -> Any:
    """Приложение aiohttp: статика, логин, разделы панели."""
    from aiohttp import web  # noqa: PLC0415 — в cli-контейнере aiohttp нет
    @web.middleware
    async def trace(request: Any, handler: Any) -> Any:
        """Проставляет correlation-id на каждый запрос и кладёт его в заголовок.

        Один идентификатор связывает веб, CRM и запись в аудите: по нему
        находится вся цепочка одного клика, а не «примерно в 14:32».
        """
        import time  # noqa: PLC0415

        from admin_core.logging_setup import (  # noqa: PLC0415
            log_event,
            new_request_id,
            request_id,
            setup_logging,
        )

        log = setup_logging("web")
        cid = request.headers.get("X-Request-Id") or new_request_id()
        token = request_id.set(cid)
        started = time.perf_counter()
        try:
            response = await handler(request)
            elapsed = int((time.perf_counter() - started) * 1000)
            log_event(
                log, 20, "request",
                cid=cid, path=request.path, method=request.method,
                status=getattr(response, "status", 0), ms=elapsed,
            )
            try:
                response.headers["X-Request-Id"] = cid
            except Exception:  # noqa: BLE001,S110 — у stream-ответов нет заголовков
                pass
            return response
        except Exception as exc:
            log_event(
                log, 40, "request failed", cid=cid, path=request.path,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        finally:
            request_id.reset(token)

    @web.middleware
    async def security(request: Any, handler: Any) -> Any:
        """Заголовки безопасности на каждый ответ и редирект HTTP → HTTPS."""
        target = https_redirect_target(
            request, tls_enabled=tls_enabled, allowed_hosts=allowed_hosts
        )
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
        if not await panel.authorized(session_token):
            return web.Response(text=login_page(), content_type="text/html")
        session = await panel.session_of(session_token)
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
        session = await panel.session_of(session_token)
        if not await panel.authorized(session_token):
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
            panel.pool, str(salon["id"]), form, actor=actor,
            actor_role=role, actor_login=session.login,
        )
        once = result.get("once")
        if once:
            # пароль и секрет 2FA не идут в URL — покажем на следующей странице
            panel.push_once(session_token, once)
        back = safe_redirect(str(form.get("back", "")), default="/today")
        if "salon=" not in back:
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
        login = str(form.get("login", "")).strip()[:200]
        password = str(form.get("password", ""))[:200]
        code = str(form.get("code", ""))[:10]
        ip = request.remote or "unknown"
        if panel.login_blocked(login, ip):
            panel.metrics.errors.labels("login_throttled").inc()
            return web.Response(
                text=login_page(
                    "Слишком много попыток входа. Попробуйте через 15 минут.",
                    login=login,
                ),
                content_type="text/html",
                status=429,
            )
        token, error = await panel.login_user(login, password, code)
        if error == "need_totp":
            # неверный код 2FA тоже считаем попыткой: иначе перебирают миллион кодов
            if code:
                panel.note_login_failure(login, ip)
            return web.Response(
                text=login_page(
                    "Введите код из приложения-аутентификатора",
                    login=login, need_totp=True,
                ),
                content_type="text/html",
            )
        if not token:
            panel.note_login_failure(login, ip)
            return web.Response(
                text=login_page("Неверный логин или пароль", login=login),
                content_type="text/html",
            )
        panel.note_login_success(login, ip)
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
        await panel.logout(request.cookies.get(SESSION_COOKIE, ""))
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

    async def export_csv(request: Any) -> Any:
        """Выгрузка отчёта в CSV — то, что отдают бухгалтеру и в налоговую.

        Права те же, что у отчёта: роли без ``reports`` получают 403, а не файл.
        Файл содержит ПДн клиентов по косвенным признакам, поэтому отдаём его
        с ``no-store`` и без кэша — как остальные страницы панели.
        """
        from admin_core import exports  # noqa: PLC0415

        session_token = request.cookies.get(SESSION_COOKIE, "")
        session = await panel.session_of(session_token)
        if not await panel.authorized(session_token) or session is None:
            return web.Response(status=302, headers={"Location": "/login"})
        if not can(session.role, "reports"):
            panel.metrics.errors.labels("forbidden").inc()
            return web.Response(status=403, text="Недостаточно прав на выгрузку")
        salon, _salons = await panel.salon_for_session(request, session)
        if salon is None:
            return web.Response(status=404, text="Нет подключённых точек")
        tz = await queries.salon_tz(panel.pool, str(salon["id"]))
        current, _previous = reports.resolve_period(request.query.get("period"), tz)
        kind = request.query.get("kind") or "daily"
        if kind == "masters":
            payload = await exports.export_masters(
                panel.pool, salon, current.start, current.end
            )
        else:
            payload = await exports.export_daily(
                panel.pool, salon, current.start, current.end
            )
        return web.Response(
            body=payload,
            # charset передаётся отдельным аргументом: aiohttp запрещает его
            # внутри content_type и отвечает 500 — выгрузка не работала ни разу.
            content_type="text/csv",
            charset="utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="otchet-{kind}-'
                    f'{datetime.now(tz).strftime("%Y%m%d")}.csv"'
                ),
                "Cache-Control": "no-store",
            },
        )

    async def accounting_export(request: Any) -> Any:
        """Бухгалтерские выгрузки: /accounting.csv?kind=payments|clients|debtors.

        Реестр счетов и клиентов видит вся сеть (роль team). Право — ``billing``,
        а не ``reports``: в выгрузке счетов есть ИНН и реквизиты плательщика, и
        отдавать их владельцу салона было бы утечкой расчёта с нами.
        """
        from admin_core import exports  # noqa: PLC0415

        session_token = request.cookies.get(SESSION_COOKIE, "")
        session = await panel.session_of(session_token)
        if not await panel.authorized(session_token) or session is None:
            return web.Response(status=302, headers={"Location": "/login"})
        if not can(session.role, "billing"):
            panel.metrics.errors.labels("forbidden").inc()
            return web.Response(status=403, text="Недостаточно прав на выгрузку")
        kind = request.query.get("kind") or "payments"
        salon, _salons = await panel.salon_for_session(request, session)
        if salon is None:
            return web.Response(status=404, text="Нет подключённых точек")
        if kind == "clients":
            payload = exports.clients_csv(
                await exports.clients_registry(panel.pool, salon_id=str(salon["id"]))
            )
            name = "klienty"
        elif kind == "debtors":
            payload = exports.to_csv(
                [
                    "Салон", "Плательщик", "Тип", "ИНН", "Тариф",
                    "Неоплаченных счетов", "Долг", "Оплачено до", "Просрочено с",
                ],
                [
                    [
                        row["name"], row["legal_name"],
                        {"self_employed": "самозанятый", "ip": "ИП",
                         "llc": "ООО"}.get(str(row["client_type"]), "—"),
                        row["inn"], row["plan"], row["unpaid"],
                        f"{float(row['debt'] or 0):.2f}",
                        row["paid_until"] or "", row["oldest_due"] or "",
                    ]
                    for row in await exports.debtors(panel.pool)
                ],
            )
            name = "dolgi"
        else:
            payload = exports.payments_csv(
                await exports.payments_registry(panel.pool)
            )
            name = "scheta"
        return web.Response(
            body=payload,
            content_type="text/csv",
            charset="utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{name}.csv"',
                "Cache-Control": "no-store",
            },
        )

    sections = {
        "/today": _today, "/bookings": _bookings, "/clients": _clients,
        "/services": _services, "/masters": _masters, "/dialogs": _dialogs,
        "/audit": _audit, "/settings": _settings, "/reports": _reports,
        "/billing": _billing,
        "/network": _network,
    }
    check_builder_signatures(sections)  # падаем на старте, а не в HTTP
    app = web.Application(middlewares=[trace, security])
    app.router.add_get("/", index)
    app.router.add_get("/login", login_get)
    app.router.add_post("/login", login_post)
    app.router.add_get("/logout", logout)
    app.router.add_get("/salon", set_salon)
    app.router.add_post("/actions", do_action)
    app.router.add_get("/static.css", static_css)
    app.router.add_get("/export.csv", export_csv)
    app.router.add_get("/accounting.csv", accounting_export)
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
    panel = Panel(pool, metrics=ServiceMetrics("web-service"))
    allowed_hosts = tuple(
        h.strip() for h in os.getenv("WEB_PUBLIC_HOST", "").split(",") if h.strip()
    )
    if allowed_hosts:
        print(f"[web] разрешённые домены панели: {allowed_hosts}", flush=True)
    else:
        print(
            "[web] ВНИМАНИЕ: WEB_PUBLIC_HOST не задан — Host-заголовок принимается как есть",
            flush=True,
        )

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
        build_app(
            panel, tls_enabled=tls_enabled, trust_proxy=trust_proxy,
            allowed_hosts=allowed_hosts,
        )
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
            # Сессии и счётчики перебора живут в памяти процесса: без уборки
            # они растут всю жизнь сервиса (это и утечка памяти, и DoS).
            await panel.purge_expired_sessions_db()
    finally:
        await health.cleanup()
        await runner.cleanup()
        await pool.close()


if __name__ == "__main__":
    import asyncio  # noqa: PLC0415

    asyncio.run(main())
