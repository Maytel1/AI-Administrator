"""Правки из панели: разбор формы → вызов CRM → понятный результат.

Здесь только HTTP-забота (валидация ввода, коды ошибок, тексты для человека);
бизнес-правила и аудит — в ``admin_core.crm.internal.InternalCRM``.
Все проверки обязаны передавать ``salon_id`` — чужая запись не правится.
"""

from __future__ import annotations

import uuid
from datetime import time
from typing import Any

from admin_core.crm.internal import InternalCRM

MIN_PRICE = 0
MAX_PRICE = 10_000_000
MIN_DURATION = 5
MAX_DURATION = 600

ERRORS = {
    "not_found": "Запись не найдена в этой точке (возможно, она уже удалена)",
    "bad_status": "Такого статуса не существует",
    "bad_id": "Некорректный идентификатор записи",
    "bad_weekday": "Нет такого дня недели",
    "bad_price": f"Цена должна быть от {MIN_PRICE} до {MAX_PRICE:,.0f} ₽".replace(",", " "),
    "bad_duration": f"Длительность — от {MIN_DURATION} до {MAX_DURATION} минут",
    "bad_time": "Время в формате ЧЧ:ММ, например 10:00",
    "nothing_to_update": "Нечего сохранять",
    "save_failed": "Не сохранилось — попробуйте ещё раз",
}

WEEKDAY_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def role_label(role: str) -> str:
    """Человеческое название роли (импорт внутри — чтобы не тянуть цикл)."""
    from .roles import role_label as _label  # noqa: PLC0415 — локальный реэкспорт

    return _label(role)


def _actor_role(actor: str) -> str:
    """Роль из строки актора «префикс:логин» (её формирует audit_actor).

    Внимание: ``audit_actor`` пишет роль ``team`` как ``admin`` (человек в
    логах), а логин может содержать двоеточие. Поэтому разбираем строку
    целиком: неизвестный префикс или лишние двоеточия → гость, без прав.
    """
    prefix, sep, login = (actor or "").partition(":")
    if not sep or ":" in login:
        return "guest"
    return {"owner": "owner", "staff": "staff", "team": "team",
            "admin": "team"}.get(prefix, "guest")


def _actor_login(actor: str) -> str:
    """Логин актора из «роль:логин» — источник сессии, не форма."""
    _role, sep, login = (actor or "").partition(":")
    return login if sep else ""


def _int(form: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(str(form.get(key, default)).strip() or default)
    except ValueError:
        return default


def _hhmm(form: dict[str, Any], key: str) -> time | None:
    """'10:0' → '10:00'; мусор и пустое значение отбрасываем."""
    raw = str(form.get(key, "")).strip()
    if not raw:
        return None
    try:
        hour, _, minute = raw.partition(":")
        hh, mm = int(hour), int(minute)
    except ValueError:
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return time(hh, mm)


def _hhmm_text(value: time | None) -> str:
    """time → 'ЧЧ:ММ' — для текста сообщения администратору."""
    return value.strftime("%H:%M") if value else "—"


def ok(message: str) -> dict[str, Any]:
    return {"ok": True, "message": message}


def fail(code: str, message: str = "") -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message or ERRORS.get(code, code)}


async def apply_form(
    pool: Any, salon_id: str, form: dict[str, Any], *, actor: str = "owner:panel",
    actor_role: str | None = None, actor_login: str = "",
) -> dict[str, Any]:
    """Единая точка входа для всех форм панели.

    ``form["action"]`` — что делаем; остальные поля зависят от действия.
    ``actor`` — кто именно правит: роль и логин попадают в ``audit_log``.
    ``actor_role`` / ``actor_login`` — из сессии; если их не передали, берёмся
    из ``actor`` (тесты и CLI). Проверку роли делает вызывающий (HTTP-слой) —
    здесь только бизнес-правила.
    """
    crm = InternalCRM(pool)
    action = str(form.get("action", ""))
    role = actor_role or _actor_role(actor)
    who = actor_login or _actor_login(actor)

    if action == "booking_status":
        booking_id = _uuid(form.get("booking_id"))
        status = str(form.get("status", ""))
        if not booking_id:
            return fail("bad_id")
        result = await crm.set_booking_status(salon_id, booking_id, status, actor=actor)
        if not result["ok"]:
            return fail(result.get("error", "save_failed"))
        label = {
            "confirmed": "Запись подтверждена",
            "completed": "Запись отмечена как выполненная",
            "cancelled": "Запись отменена — слот освободился",
            "no_show": "Отмечено: клиент не пришёл",
            "booked": "Запись снова активна",
        }.get(status, "Статус записи обновлён")
        return ok(label)

    if action == "service_save":
        service_id = _uuid(form.get("service_id"))
        if not service_id:
            return fail("bad_id", "Некорректный идентификатор услуги")
        price = _decimal(form.get("price"))
        duration = _int(form, "duration_min", 0)
        active = "is_active" in form
        if price is None or not MIN_PRICE <= price <= MAX_PRICE:
            return fail("bad_price")
        if not MIN_DURATION <= duration <= MAX_DURATION:
            return fail("bad_duration")
        result = await crm.update_service(
            salon_id, service_id, price=price, duration_min=duration,
            is_active=active, actor=actor,
        )
        if not result["ok"]:
            return fail(result.get("error", "save_failed"))
        state = "активна" if active else "выключена"
        return ok(f"«{result['title']}» сохранена: {_money(price)}, {duration} мин, {state}")

    if action == "client_save":
        client_id = _uuid(form.get("client_id"))
        if not client_id:
            return fail("bad_id", "Некорректный идентификатор клиента")
        notes = str(form.get("notes", "")).strip()
        prepay = "requires_prepayment" in form
        result = await crm.set_client_flags(
            salon_id, client_id, requires_prepayment=prepay, notes=notes, actor=actor
        )
        if not result["ok"]:
            return fail(result.get("error", "save_failed"))
        prepay_word = "предоплата включена" if prepay else "предоплата выключена"
        return ok(f"Клиент {result['phone']}: {prepay_word}, заметки сохранены")

    if action == "hours_save":
        master_id = _uuid(form.get("master_id"))
        if not master_id:
            return fail("bad_id", "Некорректный идентификатор мастера")
        weekday = _int(form, "weekday", -1)
        enabled = "enabled" in form
        start, end = _hhmm(form, "start"), _hhmm(form, "end")
        if not 0 <= weekday <= 6:
            return fail("bad_weekday")
        if enabled and (start is None or end is None):
            return fail("bad_time")
        if enabled and start and end and start >= end:
            return fail("bad_time", "Начало должно быть раньше конца")
        outside = 0
        if enabled and start and end:
            outside = await crm.count_bookings_outside_hours(
                salon_id, master_id, weekday, start, end
            )
        result = await crm.set_working_hours(
            salon_id, master_id, weekday, start=start, end=end, enabled=enabled, actor=actor
        )
        if not result["ok"]:
            return fail(result.get("error", "save_failed"))
        day = WEEKDAY_RU[weekday]
        if not enabled:
            return ok(f"{result['master']}: {day} — нерабочий")
        text = f"{result['master']}: {day} {_hhmm_text(start)}–{_hhmm_text(end)}"
        if outside:
            text += f". Внимание: {outside} записей выпадут из окна"
        return ok(text)

    if action == "service_create":
        return await _service_create(pool, salon_id, form, actor=actor)

    if action == "service_archive":
        service_id = _uuid(form.get("service_id"))
        if not service_id:
            return fail("bad_id", "Некорректный идентификатор услуги")
        active = str(form.get("active", "")) == "1"
        result = await crm.set_service_active(
            salon_id, service_id, active=active, actor=actor
        )
        if not result["ok"]:
            return fail(result["error"])
        state = "вернулась в прайс" if active else "убрана из прайса"
        return ok(f"«{result['title']}» {state}")

    if action == "master_create":
        result = await crm.create_master(
            salon_id, name=str(form.get("name", "")), actor=actor
        )
        if not result["ok"]:
            return fail(result["error"], result["message"])
        return ok(f"Мастер {result['name']} добавлен — задайте рабочие дни")

    if action == "master_archive":
        master_id = _uuid(form.get("master_id"))
        if not master_id:
            return fail("bad_id", "Некорректный идентификатор мастера")
        active = str(form.get("active", "")) == "1"
        result = await crm.set_master_active(
            salon_id, master_id, active=active, actor=actor
        )
        if not result["ok"]:
            return fail(result["error"], result["message"])
        state = "вернулся в работу" if active else "отключён"
        return ok(f"Мастер {result['name']} {state}")

    if action == "client_create":
        return await _client_create(pool, salon_id, form, actor=actor)

    if action == "waitlist_add":
        return await _waitlist_add(pool, salon_id, form, actor=actor)

    if action == "waitlist_drop":
        from ..waitlist import drop_from_waitlist  # noqa: PLC0415

        wid = _uuid(form.get("waitlist_id"))
        if not wid:
            return fail("bad_id", "Некорректный идентификатор записи очереди")
        result = await drop_from_waitlist(pool, salon_id, wid, actor=actor)
        if not result["ok"]:
            return fail("not_found")
        return ok("Клиент убран из листа ожидания")

    if action == "user_create":
        # пароль не приходит из браузера: генерируем и показываем один раз
        return await _user_create(pool, salon_id, form, actor=actor)

    if action == "user_totp":
        return await _user_totp(pool, salon_id, form, actor=actor, role=role)

    if action == "user_disable":
        from .users import disable_user  # noqa: PLC0415 — только для этого действия

        user_id = _uuid(form.get("user_id"))
        if not user_id:
            return fail("bad_id", "Некорректный идентификатор пользователя")
        result = await disable_user(
            pool, user_id=user_id, actor=actor, salon_id=salon_id,
            actor_role=role, actor_login=who,
        )
        if not result["ok"]:
            if result["error"] in ("last_owner", "self_disable"):
                return fail(result["error"], result["message"])
            return fail("not_found")
        return ok("Доступ отключён")

    return fail("save_failed", "Неизвестное действие формы")


async def _service_create(
    pool: Any, salon_id: str, form: dict[str, Any], *, actor: str
) -> dict[str, Any]:
    """Новая услуга из панели: название, длительность, цена."""
    crm = InternalCRM(pool)
    price = _decimal(form.get("price"))
    duration = _int(form, "duration_min", 0)
    if price is None or not MIN_PRICE <= price <= MAX_PRICE:
        return fail("bad_price")
    if not MIN_DURATION <= duration <= MAX_DURATION:
        return fail("bad_duration")
    result = await crm.create_service(
        salon_id, title=str(form.get("title", "")), duration_min=duration,
        price=price, actor=actor,
    )
    if not result["ok"]:
        return fail(result["error"], result["message"])
    return ok(f"Услуга «{result['title']}» добавлена: {_money(price)}, {duration} мин")


async def _waitlist_add(
    pool: Any, salon_id: str, form: dict[str, Any], *, actor: str
) -> dict[str, Any]:
    """Клиент в лист ожидания: освободившийся слот уйдёт ему автоматически."""
    from ..waitlist import add_to_waitlist, list_waitlist  # noqa: PLC0415

    phone = str(form.get("phone", "")).strip()
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 10:
        return fail("bad_phone", "Телефон нужен полностью — например +79001234567")
    client = await InternalCRM(pool).ensure_client(salon_id, phone, None)
    service_id = _uuid(form.get("service_id")) or None
    await add_to_waitlist(
        pool, salon_id, str(client["id"]), service_id=service_id,
        note=str(form.get("note", "")).strip() or None,
    )
    queue = len(await list_waitlist(pool, salon_id))
    return ok(f"Клиент в листе ожидания (в очереди {queue}) — вернём, как только освободится")


async def _client_create(
    pool: Any, salon_id: str, form: dict[str, Any], *, actor: str
) -> dict[str, Any]:
    """Ручной ввод клиента: телефон обязателен, он же ключ «своего» клиента."""
    crm = InternalCRM(pool)
    phone = str(form.get("phone", "")).strip()
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 10:
        return fail("bad_phone", "Телефон нужен полностью — например +79001234567")
    result = await crm.create_client_manual(
        salon_id, phone=phone, name=str(form.get("name", "")).strip() or None,
        notes=str(form.get("notes", "")).strip() or None,
        requires_prepayment="requires_prepayment" in form, actor=actor,
    )
    return ok(f"Клиент {result['phone']} сохранён — бот узнает его по номеру")


async def _user_create(
    pool: Any, salon_id: str, form: dict[str, Any], *, actor: str
) -> dict[str, Any]:
    """Новый сотрудник точки: e-mail, роль, временный пароль (показываем один раз)."""
    from .auth import random_password  # noqa: PLC0415
    from .roles import SELF_ISSUE_ROLES  # noqa: PLC0415
    from .users import create_user  # noqa: PLC0415

    role = str(form.get("role", "staff"))
    if role not in SELF_ISSUE_ROLES:
        return fail("bad_role", "Из панели можно выдать только роли «владелец» и «администратор»")
    password = random_password()
    result = await create_user(
        pool, login=str(form.get("login", "")), password=password, role=role,
        salon_id=salon_id, name=str(form.get("name", "")).strip() or None, actor=actor,
    )
    if not result["ok"]:
        return fail(result["error"], result["message"])
    return {
        "ok": True,
        "message": f"{result['login']} создан(а), роль {role_label(role)}",
        "once": {
            "login": result["login"],
            "password": password,
            "note": "Пароль показывается один раз — передайте сотруднику лично",
        },
    }


async def _user_totp(
    pool: Any, salon_id: str, form: dict[str, Any], *, actor: str,
    role: str = "owner",
) -> dict[str, Any]:
    """Включение 2FA: выдаём секрет и ссылку для приложения — один раз.

    Секрет показывается только тому, кто вправе управлять этой учёткой:
    своей точки (или любой, если роль ``team``). Иначе владелец точки
    получал бы чужой секрет 2FA и мог бы войти за другого сотрудника.
    """
    from .auth import generate_totp_secret, provisioning_uri  # noqa: PLC0415
    from .users import find_user_in_salon, set_totp  # noqa: PLC0415

    login = str(form.get("login", ""))
    row = await find_user_in_salon(pool, login, salon_id=salon_id, actor_role=role)
    if not row:
        return fail("not_found", "Такого пользователя нет в вашей точке")
    secret = generate_totp_secret()
    saved = await set_totp(
        pool, str(row["id"]), secret=secret, enabled=True, actor=actor,
        salon_id=salon_id, actor_role=role,
    )
    if not saved["ok"]:
        return fail("not_found", "Такого пользователя нет в вашей точке")
    return {
        "ok": True,
        "message": f"2FA включена для {login}",
        "once": {
            "login": login,
            "secret": secret,
            "uri": provisioning_uri(secret, login),
            "note": "Секрет показывается один раз. Отсканируйте QR в приложении-аутентификаторе",
        },
    }


def _decimal(raw: Any) -> float | None:
    """'2 000,50' / '2000.5' → float; мусор → None, чтобы показать свою ошибку."""
    text = str(raw or "").replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _uuid(raw: Any) -> str:
    """Проверяем UUID до похода в БД.

    Без этой проверки мусорный ``service_id=«'; DROP TABLE …»`` доводит asyncpg до
    ``DataError`` и роняет обработчик POST в 500: это отказ в обслуживании по
    одному клику, а не защита от инъекции.
    """
    text = str(raw or "").strip()
    try:
        return str(uuid.UUID(text))
    except (ValueError, AttributeError):
        return ""


def _money(value: float) -> str:
    return f"{int(round(value)):,}".replace(",", " ") + " ₽"
