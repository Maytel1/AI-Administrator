"""Хранилище пользователей панели: создание, проверка входа, список команды.

Вход = логин + пароль (+ TOTP, если включён). Проверка 2FA обязательна для роли
``team`` и доступна (включается) для владельца точки: пароль утекает чаще,
а код из телефона — нет.
"""

from __future__ import annotations

from typing import Any

from .auth import hash_password, random_password, verify_password, verify_totp
from .roles import ROLES, audit_actor


async def create_user(
    pool: Any,
    *,
    login: str,
    password: str,
    role: str,
    salon_id: str | None = None,
    name: str | None = None,
    totp_secret: str | None = None,
    totp_enabled: bool = False,
    actor: str = "system",
) -> dict[str, Any]:
    """Создаёт пользователя. Возвращает данные для показа один раз (пароль/секрет)."""
    if role not in ROLES:
        return {"ok": False, "error": "bad_role", "message": f"Неизвестная роль: {role}"}
    login = (login or "").strip().lower()
    if "@" not in login or "." not in login.split("@")[-1]:
        return {"ok": False, "error": "bad_login", "message": "Логин должен быть e-mail"}
    if role != "team" and not salon_id:
        return {
            "ok": False, "error": "no_salon",
            "message": "Владелец и администратор привязаны к точке",
        }
    stored = hash_password(password)
    try:
        row = await pool.fetchrow(
            "INSERT INTO panel_users (salon_id, login, name, role, password_hash, "
            "totp_secret, totp_enabled) VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id, login",
            salon_id, login, name, role, stored, totp_secret, totp_enabled,
        )
    except Exception as exc:  # noqa: BLE001 — unique violation приходит из asyncpg
        if "panel_users_login_key" in str(exc) or "unique" in str(exc).lower():
            return {
                "ok": False, "error": "exists", "message": f"Логин {login} уже занят"
            }
        raise
    await pool.execute(
        "INSERT INTO audit_log (salon_id, actor, action, details) "
        "VALUES ($1, $2, 'create_panel_user', $3)",
        salon_id, actor,
        f'{{"login": "{login}", "role": "{role}", "2fa": {str(totp_enabled).lower()}}}',
    )
    return {"ok": True, "id": str(row["id"]), "login": login, "role": role}


async def authenticate(
    pool: Any, login: str, password: str, code: str | None = None
) -> dict[str, Any]:
    """Проверка входа. Возвращает пользователя без хеша и секрета.

    Ошибки специально разные для пользователя и для сервиса, но наружу отдаём
    одну: «неверный логин или пароль» — чтобы не перебором узнать, кто есть.
    """
    row = await pool.fetchrow(
        "SELECT id, salon_id, login, name, role, password_hash, totp_secret, totp_enabled "
        "FROM panel_users WHERE login = $1 AND is_active",
        (login or "").strip().lower(),
    )
    generic = {"ok": False, "error": "invalid", "message": "Неверный логин или пароль"}
    if not row or not verify_password(password, row["password_hash"]):
        return generic
    if row["totp_enabled"]:
        if not code or not verify_totp(row["totp_secret"], code):
            return {
                "ok": False, "error": "need_totp",
                "message": "Введите код из приложения-аутентификатора",
            }
    await pool.execute(
        "UPDATE panel_users SET last_login_at = now() WHERE id = $1", row["id"]
    )
    return {
        "ok": True,
        "user": {
            "id": str(row["id"]),
            "salon_id": str(row["salon_id"]) if row["salon_id"] else None,
            "login": row["login"],
            "name": row["name"],
            "role": row["role"],
        },
    }


async def list_users(pool: Any, salon_id: str) -> list[dict[str, Any]]:
    """Команда точки: логины, роли, включён ли 2FA, когда последний вход."""
    rows = await pool.fetch(
        "SELECT login, name, role, totp_enabled, last_login_at, created_at "
        "FROM panel_users WHERE salon_id = $1 AND is_active ORDER BY role, login",
        salon_id,
    )
    return [dict(r) for r in rows]


async def set_totp(
    pool: Any, user_id: str, *, secret: str, enabled: bool, actor: str
) -> dict[str, Any]:
    """Включение/выключение 2FA для пользователя."""
    await pool.execute(
        "UPDATE panel_users SET totp_secret = $2, totp_enabled = $3 WHERE id = $1",
        user_id, secret, enabled,
    )
    await pool.execute(
        "INSERT INTO audit_log (actor, action, details) "
        "VALUES ($1, 'set_panel_totp', $2)",
        actor, f'{{"user_id": "{user_id}", "enabled": {str(enabled).lower()}}}',
    )
    return {"ok": True, "enabled": enabled}


async def grant_temporary_password(
    pool: Any, login: str, role: str, salon_id: str | None
) -> dict[str, Any]:
    """Первый вход владельца: временный пароль, TOTP-секрет печатается один раз."""
    password = random_password()
    result = await create_user(
        pool, login=login, password=password, role=role, salon_id=salon_id
    )
    if not result["ok"]:
        return result
    return {**result, "password": password}


def actor_for(role: str, login: str) -> str:
    """Актор для audit_log по пользователю панели."""
    return audit_actor(role, login)
