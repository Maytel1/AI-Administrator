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
from urllib.parse import quote, urlencode, urlsplit
from zoneinfo import ZoneInfo

from admin_core import acquiring
from admin_core.bot_quality import bot_quality
from admin_core.db import get_pool, run_migrations
from admin_core.health import ServiceMetrics, make_health_app, start_health_server
from admin_core.web import actions, dashboard, queries, reports, users
from admin_core.web.legal import oferta_page, pdn_page
from admin_core.web.onboarding import onboarding_steps
from admin_core.web.pages_core import page_bookings, page_today
from admin_core.web.pages_lists import (
    page_audit,
    page_billing,
    page_clients,
    page_counterparties,
    page_dialogs,
    page_masters,
    page_network,
    page_pay,
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
    LOGIN_COOKIE,
    REMEMBER_TTL,
    SESSION_COOKIE,
    PageCtx,
    Panel,
    check_builder_signatures,
)

STATIC_DIR = Path(__file__).parent

# Варианты периода для ленты записей: (дней назад/вперёд, подпись).
# Показываем столько же, сколько просматриваем, иначе подпись вводит в
# заблуждение: «7 дней» при фильтре на 30 — это молчаливое враньё.
PERIOD_CHOICES: tuple[tuple[int, str], ...] = (
    (1, "Сегодня"),
    (7, "Недеделя"),
    (30, "Месяц"),
    (90, "Квартал"),
)
PERIOD_DAYS = {days for days, _ in PERIOD_CHOICES}


async def _today(
    panel: Panel, salon: dict, salons: list[dict], request: Any, ctx: PageCtx | None = None
) -> str:
    """Дашборд дня: сутки салона по его таймзоне."""
    tz = await queries.salon_tz(panel.pool, str(salon["id"]))
    start, end = queries.day_bounds(tz)
    stats = await dashboard.today_stats(panel.pool, str(salon["id"]), start, end)
    schedule = await dashboard.schedule_for_day(panel.pool, str(salon["id"]), start, end)
    # Чек-лист «Первые шаги» для новой точки: что настроено, что осталось.
    onboarding = dict(await onboarding_steps(panel.pool, salon))
    panel.metrics.messages.labels("page_view").inc()
    msg, err = _flash_from(request)
    return page_today(stats, schedule, tz, salon, salons, ctx, msg, err, onboarding)


def _flash_from(request: Any) -> tuple[str, bool]:
    """Результат прошлой правки перекладываем в query после редиректа."""
    return str(request.query.get("msg", "")), request.query.get("err") == "1"


async def _bookings(
    panel: Panel, salon: dict, salons: list[dict], request: Any, ctx: PageCtx | None = None
) -> str:
    "ента записей вокруг «сей-now»; период — из query, статус — тоже."""
    tz = await queries.salon_tz(panel.pool, str(salon["id"]))
    now = datetime.now(tz)
    # Период задаёт администратор: по умолчанию месяц в каждую сторону.
    # Мусор в query не должен ломать страницу — молча берём значение по умолчанию.
    try:
        days = int(request.query.get("days", "30"))
    except ValueError:
        days = 30
    days = days if days in PERIOD_DAYS else 30
    start = (now - timedelta(days=days)).astimezone(ZoneInfo("UTC"))
    end = (now + timedelta(days=days)).astimezone(ZoneInfo("UTC"))
    status = request.query.get("status", "")
    rows = await queries.bookings_between(panel.pool, str(salon["id"]), start, end, status)
    msg, err = _flash_from(request)
    return page_bookings(rows, tz, status, salon, salons, ctx, msg, err, days=days)


async def _clients(
    panel: Panel, salon: dict, salons: list[dict], request: Any, ctx: PageCtx | None = None
) -> str:
    """Клиенты точки + лист ожидания: кому предложим освободившийся слот."""
    from admin_core.waitlist import list_waitlist  # noqa: PLC0415

    tz = await queries.salon_tz(panel.pool, str(salon["id"]))
    rows = await queries.clients_list(panel.pool, str(salon["id"]))
    queue = await list_waitlist(panel.pool, str(salon["id"]))
    services = await queries.services_list(panel.pool, str(salon["id"]))
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
    # Прайс нужен окну «Услуги мастера»: чекбоксы — весь прайс салона.
    price = await queries.services_list(panel.pool, str(salon["id"]))
    msg, err = _flash_from(request)
    return page_masters(rows, salon, salons, ctx, msg, err, price)


async def _dialogs(
    panel: Panel, salon: dict, salons: list[dict], request: Any, ctx: PageCtx | None = None
) -> str:
    "ента диалогов; ``?id=<dialog_id>`` — раскрыть переписку."""
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
    # Список выбора контрагента — только из реестра (источник данных счёта).
    from admin_core import counterparties  # noqa: PLC0415

    cp_list = await counterparties.active_options(panel.pool)
    # Именованные аргументы: позиционный вызов подсовывал flash в `legal`,
    # а ctx в `flash` — карточка плательщика исчезала, а в лог шёл мусор.
    return page_billing(
        status, salon, salons, history,
        flash=msg, flash_error=err, ctx=ctx, counterparties=cp_list,
    )


async def _counterparties(
    panel: Panel, salon: dict, salons: list[dict], request: Any, ctx: PageCtx | None = None
) -> str:
    """Реестр контрагентов: общий на команду справочник подключённых клиентов.

    Его данные питают документы (счёт/акт) и списки выбора. Правит только
    роль team — проверка здесь, а не только в меню: пункт скрыт, а URL открыт.
    """
    from admin_core import counterparties  # noqa: PLC0415

    role = ctx.role if ctx else "guest"
    if not can(role, "counterparties"):
        panel.metrics.errors.labels("forbidden").inc()
        return page_counterparties(
            [], None, salons, "",
            flash="Недостаточно прав на раздел «Контрагенты»",
            flash_error=True, ctx=ctx,
        )
    csrf = ctx.csrf if ctx else ""
    items = await counterparties.list_all(panel.pool)
    # ?edit=<id> — правка существующего: подставляем его данные в форму.
    edit_id = str(request.query.get("edit", ""))
    editing = await counterparties.get(panel.pool, edit_id) if edit_id else None
    msg, err = _flash_from(request)
    return page_counterparties(
        items, editing, salons, csrf,
        flash=msg, flash_error=err, ctx=ctx,
    )


async def _pay(
    panel: Panel, salon: dict, salons: list[dict], request: Any, ctx: PageCtx | None = None
) -> str:
    """Оплата тарифа точки: статус, лимиты, история и реквизиты.

    Вариант раздела для салона: владелец видит свой счёт и платит по
    реквизитам. Реквизиты плательщика (ИНН и т.п.) и внутренняя админка
    (счета, долги, CSV) — на /billing, карточки плательщика здесь нет.
    """
    from admin_core import billing  # noqa: PLC0415

    role = ctx.role if ctx else "guest"
    # владелец платит сам (pay), команда управляет всем (billing)
    if not (can(role, "pay") or can(role, "billing")):
        panel.metrics.errors.labels("forbidden").inc()
        return page_pay(
            {"status_text": "Недостаточно прав: раздел для владельца точки",
             "price": 0, "plan": "", "requisites": {},
             "is_trial": False, "expired": False},
            salon, salons,
            flash="Недостаточно прав на раздел «Оплата»",
            flash_error=True, ctx=ctx,
        )

    salon_row = dict(await panel.pool.fetchrow(
        "SELECT id, name, plan, paid_until, timezone FROM salons WHERE id = $1",
        salon["id"],
    ))
    status = dict(await billing.billing_status(panel.pool, salon_row))
    # Кнопка «Оплатить картой» — только когда в .env заданы ключи эквайринга.
    # Без них работаем в ручном режиме (перевод по реквизитам), как раньше.
    status["card_enabled"] = acquiring.provider_from_env() is not None
    # Использование лимитов тарифа: полоски и предупреждения на странице оплаты.
    from admin_core import plans  # noqa: PLC0415

    status["usage"] = dict(await plans.usage_report(panel.pool, salon_row))
    history = await panel.pool.fetch(
        "SELECT period_start, period_end, amount, status, paid_at, invoice_no "
        "FROM payments WHERE salon_id = $1 ORDER BY period_start DESC LIMIT 24",
        salon["id"],
    )
    status["history"] = [dict(row) for row in history]
    msg, err = _flash_from(request)
    return page_pay(
        status, salon, salons,
        flash=msg, flash_error=err, ctx=ctx,
    )


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

    async def pay_card(request: Any) -> Any:
        """POST /pay/card — выставить счёт, создать платёж у ЮKassa и увести
        владельца на страницу оплаты. Сессия и CSRF — как у обычной правки.
        """
        session_token = request.cookies.get(SESSION_COOKIE, "")
        if not await panel.authorized(session_token):
            return web.HTTPFound("/login")
        session = await panel.session_of(session_token)
        form = await request.post()
        if not panel.csrf_ok(session_token, str(form.get("csrf", ""))):
            panel.metrics.errors.labels("csrf").inc()
            return web.HTTPFound(
                "/pay?err=1&msg=" + quote("Форма устарела — обновите страницу", safe="")
            )
        role = session.role if session else "guest"
        if not (can(role, "pay") or can(role, "billing")):
            panel.metrics.errors.labels("forbidden").inc()
            return web.HTTPFound(
                "/pay?err=1&msg=" + quote(
                    f"Роли «{role_label(role)}» не хватает прав на оплату", safe=""
                )
            )
        provider_settings = acquiring.provider_from_env()
        if provider_settings is None:
            return web.HTTPFound(
                "/pay?err=1&msg=" + quote("Онлайн-оплата не настроена", safe="")
            )
        salon, _salons = await panel.salon_for_session(request, session)
        if salon is None:
            return web.HTTPFound(
                "/pay?err=1&msg=" + quote("Нет подключённых точек", safe="")
            )
        actor = audit_actor(role, session.login)
        try:
            result = await acquiring.start_card_payment(
                panel.pool, acquiring.YooKassaProvider(provider_settings),
                salon, actor=actor,
            )
        except Exception:  # noqa: BLE001 — сеть/провайдер, показываем человеку
            panel.metrics.errors.labels("payment").inc()
            return web.HTTPFound(
                "/pay?err=1&msg=" + quote(
                    "Не удалось создать платёж. Попробуйте ещё раз или "
                    "оплатите переводом по реквизитам.", safe="",
                )
            )
        if not result.get("ok"):
            return web.HTTPFound(
                "/pay?err=1&msg=" + quote(
                    str(result.get("error") or "Провайдер не вернул ссылку"), safe=""
                )
            )
        return web.HTTPFound(str(result["confirmation_url"]))

    async def pay_notify(request: Any) -> Any:
        """POST /pay/notify — уведомление от ЮKassa. Публичный (server-to-server).

        Без сессии и CSRF: провайдер стучится сам. Безопасность — не в подписи
        тела (у ЮKassa её нет), а в перечитывании платежа у провайдера и сверке
        суммы (process_webhook_notification): подделав payment.succeeded в теле,
        нельзя создать реальный успешный платёж на стороне ЮKassa. Ограничение
        по IP провайдера — на уровне nginx (фаза 3)."""
        provider_settings = acquiring.provider_from_env()
        if provider_settings is None:
            # Эквайринг выключен — 200, чтобы провайдер не ушёл в бесконечный ретрай.
            return web.json_response({"ok": False, "reason": "not_configured"})
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001 — тело не JSON, не наше уведомление
            return web.json_response({"ok": False, "reason": "bad_json"})
        provider = acquiring.YooKassaProvider(provider_settings)
        try:
            tag = await acquiring.process_webhook_notification(panel.pool, provider, payload)
        except Exception:  # noqa: BLE001 — сеть/БД; ЮKassa перешлёт повторно
            panel.metrics.errors.labels("webhook").inc()
            tag = "error"
        # 200 на любое доставленное уведомление — иначе ЮKassa ретраит бесконечно.
        return web.json_response({"ok": True, "result": tag})

    async def index(_request: Any) -> Any:
        return web.HTTPFound("/today")

    async def login_get(request: Any) -> Any:
        # Логин из «Запомнить меня» — только предзаполнение поля, не пароль.
        remembered = str(request.cookies.get(LOGIN_COOKIE, ""))[:200]
        return web.Response(
            text=login_page(login=remembered), content_type="text/html"
        )

    async def login_post(request: Any) -> Any:
        form = await request.post()
        login = str(form.get("login", "")).strip()[:200]
        password = str(form.get("password", ""))[:200]
        code = str(form.get("code", ""))[:10]
        # Согласия обязательны: оферта и обработка ПДн (152-ФЗ). Без них
        # входа нет — это юридически значимое действие, а не косметика.
        agreed = bool(form.get("agree_oferta")) and bool(form.get("agree_pdn"))
        remember = bool(form.get("remember"))
        ip = request.remote or "unknown"
        if not agreed:
            return web.Response(
                text=login_page(
                    "Чтобы войти, примите условия оферты и согласие "
                    "на обработку персональных данных",
                    login=login,
                    remember=remember,
                ),
                content_type="text/html",
                status=400,
            )
        if panel.login_blocked(login, ip):
            panel.metrics.errors.labels("login_throttled").inc()
            return web.Response(
                text=login_page(
                    "Слишком много попыток входа. Попробуйте через 15 минут.",
                    login=login,
                    agreed=True,
                    remember=remember,
                ),
                content_type="text/html",
                status=429,
            )
        token, error = await panel.login_user(
            login, password, code, remember=remember,
        )
        if error == "need_totp":
            # неверный код 2FA тоже считаем попыткой: иначе перебирают миллион кодов
            if code:
                panel.note_login_failure(login, ip)
            return web.Response(
                text=login_page(
                    "Введите код из приложения-аутентификатора",
                    login=login, need_totp=True, agreed=True, remember=remember,
                ),
                content_type="text/html",
            )
        if not token:
            panel.note_login_failure(login, ip)
            return web.Response(
                text=login_page(
                    "Неверный логин или пароль",
                    login=login, agreed=True, remember=remember,
                ),
                content_type="text/html",
            )
        panel.note_login_success(login, ip)
        resp = web.HTTPFound("/today")
        secure_cookie = cookie_secure_flag(
            secure=is_secure_request(request, tls_enabled=tls_enabled, trust_proxy=trust_proxy)
        )
        # «Запомнить меня» — cookie с max_age (30 дней) вместо session-cookie;
        # без галочки браузер забывает сессию при закрытии.
        resp.set_cookie(
            SESSION_COOKIE, token, httponly=True, samesite="Lax", path="/",
            secure=secure_cookie,
            max_age=int(REMEMBER_TTL.total_seconds()) if remember else None,
        )
        if remember:
            # Только логин для предзаполнения поля; пароль в cookie не кладём —
            # его сохранит менеджер паролей браузера (галочка + login.js).
            resp.set_cookie(
                LOGIN_COOKIE, login, httponly=True, samesite="Lax", path="/",
                secure=secure_cookie,
                max_age=int(REMEMBER_TTL.total_seconds()),
            )
        else:
            resp.del_cookie(LOGIN_COOKIE, path="/")
        return resp

    async def logout(request: Any) -> Any:
        await panel.logout(request.cookies.get(SESSION_COOKIE, ""))
        resp = web.HTTPFound("/login")
        resp.del_cookie(SESSION_COOKIE, path="/")
        return resp

    async def set_salon(request: Any) -> Any:
        """Переключение тенанта командой админки; запоминаем в cookie.

        После переключения возвращаем на ту же страницу (по Referer), а не
        всегда на «Сегодня»: иначе выбор точки с любой вкладки уводил бы
        пользователя на главную. Если Referer нет или он внешний — на «Сегодня».
        """
        data = request.query.get("id")
        target = "/today"
        referer = request.headers.get("Referer", "")
        path = urlsplit(referer).path
        if path.startswith("/") and not path.startswith("//"):
            target = path
        resp = web.HTTPFound(target)
        if data:
            resp.set_cookie("salon", str(data), httponly=True, samesite="Lax", path="/")
        return resp

    async def static_css(_request: Any) -> Any:
        """Стили отдаём сами — панель работает без внешнего CDN."""
        return web.Response(
            text=(STATIC_DIR / "static.css").read_text(encoding="utf-8"),
            content_type="text/css",
        )

    async def login_js(_request: Any) -> Any:
        """Микроскрипт страницы входа: включает сохранение пароля при «Запомнить меня».

        Отдельный файл, а не инлайн — CSP запрещает inline-скрипты.
        """
        return web.Response(
            text=(STATIC_DIR / "login.js").read_text(encoding="utf-8"),
            content_type="application/javascript",
        )

    async def salon_js(_request: Any) -> Any:
        """Микроскрипт переключателя точки: отправляет форму по change.

        Отдельный файл, а не инлайн — CSP запрещает inline-скрипты (иначе
        выбор точки ничего не делал).
        """
        return web.Response(
            text=(STATIC_DIR / "salon.js").read_text(encoding="utf-8"),
            content_type="application/javascript",
        )

    async def modal_js(_request: Any) -> Any:
        """Скрипт доступности модальных окон: Esc закрывает, фокус переходит внутрь.

        Отдельный файл, а не инлайн — CSP запрещает inline-скрипты.
        """
        return web.Response(
            text=(STATIC_DIR / "modal.js").read_text(encoding="utf-8"),
            content_type="application/javascript",
        )

    async def oferta(_request: Any) -> Any:
        """Публичная оферта — доступна без входа (ссылка на форме логина)."""
        return web.Response(text=oferta_page(), content_type="text/html")

    async def pdn(_request: Any) -> Any:
        """Соглашение об обработке ПДн — доступно без входа (152-ФЗ)."""
        return web.Response(text=pdn_page(), content_type="text/html")

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
        "/counterparties": _counterparties,
        "/pay": _pay,
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
    app.router.add_post("/pay/card", pay_card)
    app.router.add_post("/pay/notify", pay_notify)
    app.router.add_get("/static.css", static_css)
    app.router.add_get("/login.js", login_js)
    app.router.add_get("/salon.js", salon_js)
    app.router.add_get("/modal.js", modal_js)
    app.router.add_get("/oferta", oferta)
    app.router.add_get("/pdn", pdn)
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
    trust_proxy = os.getenv("WEB_TRUST_PROXY", "") == "1"
    # WEB_TRUST_PROXY=1 = перед панелью обратный прокси, и он же терминирует
    # TLS. Значит, вебу собственный TLS не нужен, а нужен там, где прокси нет.
    #
    # Раньше решение принималось по наличию файлов сертификата, и в проде веб
    # поднимался с TLS, откуда nginx (plain HTTP) получал 502. Переменными
    # compose это не лечится: значения приходят ещё и из env_file, который
    # перекрывает environment.
    tls_enabled = bool(tls_cert and tls_key) and not trust_proxy
    if tls_cert and tls_key and trust_proxy:
        print("[web] TLS off: terminated by proxy (WEB_TRUST_PROXY=1)", flush=True)
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
    asyncio.run(main())
