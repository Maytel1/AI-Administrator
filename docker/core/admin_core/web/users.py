"""Хранилище пользователей панели: создание, проверка входа, список команды.

Вход = логин + пароль (+ TOTP, если включён). Проверка 2FA обязательна для роли
``team`` и доступна (включается) для владельца точки: пароль утекает чаще,
а код из телефона — нет.
"""

from __future__ import annotations

import json
from typing import Any

from .auth import hash_password, random_password, verify_password, verify_totp
from .roles import ROLES, audit_actor

# Хеш-пустышка: считается для несуществующих логинов, чтобы время ответа
# не выдавало, какие e-mail заведены в системе.
_DECOY_HASH = hash_password("decoy-password-for-timing", rounds=120_000)


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
        json.dumps(
            {"login": login, "role": role, "2fa": totp_enabled}, ensure_ascii=False
        ),
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
    if not row:
        # Считаем хеш всё равно: иначе «нет пользователя» отвечает за
        # миллисекунды, а «есть, но не тот пароль» — за 240 тысяч итераций
        # PBKDF2. Разница в скорости ответа выдаёт список e-mail в системе.
        verify_password(password, _DECOY_HASH)
        return generic
    if not verify_password(password, row["password_hash"]):
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
        "SELECT id, login, name, role, totp_enabled, last_login_at, created_at "
        "FROM panel_users WHERE salon_id = $1 AND is_active ORDER BY role, login",
        salon_id,
    )
    return [dict(r) for r in rows]


async def find_user_in_salon(
    pool: Any, login: str, *, salon_id: str | None, actor_role: str
) -> dict[str, Any] | None:
    """Пользователь, которого актор вправе править.

    Роль ``team`` — наша команда, ей доступны все точки и служебные учётки.
    Все остальные видят только свою точку: чужой ``login`` в форме не должен
    ни включить 2FA, ни выдать секрет, ни отключить сотрудника.
    """
    if actor_role == "team":
        row = await pool.fetchrow(
            "SELECT id, login, salon_id, role FROM panel_users "
            "WHERE login = $1 AND is_active",
            (login or "").strip().lower(),
        )
    else:
        row = await pool.fetchrow(
            "SELECT id, login, salon_id, role FROM panel_users "
            "WHERE login = $1 AND is_active AND salon_id = $2::uuid",
            (login or "").strip().lower(), salon_id,
        )
    return dict(row) if row else None


async def set_totp(
    pool: Any, user_id: str, *, secret: str, enabled: bool, actor: str,
    salon_id: str | None = None, actor_role: str = "owner",
) -> dict[str, Any]:
    """Включение/выключение 2FA. Править можно только свой салон.

    ``salon_id IS NULL`` — служебная учётка нашей команды; её трогает только
    роль ``team``. Без этой проверки владелец точки мог бы включить 2FA чужому
    сотруднику и увидеть его секрет в одноразовом показе.
    """
    row = await pool.fetchrow(
        "UPDATE panel_users SET totp_secret = $3, totp_enabled = $4 "
        "WHERE id = $1 AND is_active "
        "AND (salon_id = $2::uuid OR ($5 AND salon_id IS NULL)) RETURNING id",
        user_id, salon_id, secret, enabled, actor_role == "team",
    )
    if not row:
        return {"ok": False, "error": "not_found"}
    await pool.execute(
        "INSERT INTO audit_log (salon_id, actor, action, details) "
        "VALUES ($1, $2, 'set_panel_totp', $3)",
        salon_id, actor,
        json.dumps({"user_id": user_id, "enabled": enabled}, ensure_ascii=False),
    )
    return {"ok": True, "enabled": enabled}


async def disable_user(
    pool: Any, *, user_id: str, actor: str, salon_id: str | None = None,
    actor_role: str = "owner", actor_login: str = "",
) -> dict[str, Any]:
    """Отключение доступа.

    Четыре ограничения, иначе панель ломается по-настоящему:
    * чужой салон не трогаем (тот же предикат, что в set_totp);
    * себя отключить нельзя — иначе можно случайно закрыть себе доступ;
    * последнего активного владельца своей точки не отключаем;
    * «последний владелец» считаем в салоне цели, а не в салоне актора.
    """
    target = await pool.fetchrow(
        "SELECT id, login, salon_id, role FROM panel_users WHERE id = $1 AND is_active "
        "AND (salon_id = $2::uuid OR ($3 AND salon_id IS NULL))",
        user_id, salon_id, actor_role == "team",
    )
    if not target:
        return {"ok": False, "error": "not_found"}
    if actor_login and target["login"] == actor_login:
        return {"ok": False, "error": "self_disable",
                "message": "Себя отключить нельзя"}
    if target["role"] == "owner":
        peers = await pool.fetchval(
            "SELECT count(*) FROM panel_users WHERE role = 'owner' AND is_active "
            "AND id <> $1 AND salon_id IS NOT DISTINCT FROM $2::uuid",
            target["id"], target["salon_id"],
        )
        if not peers:
            return {
                "ok": False, "error": "last_owner",
                "message": "Это последний владелец точки — отключить нельзя",
            }
    await pool.execute(
        "UPDATE panel_users SET is_active = false WHERE id = $1", target["id"]
    )
    await pool.execute(
        "INSERT INTO audit_log (salon_id, actor, action, details) "
        "VALUES ($1, $2, 'disable_panel_user', $3)",
        salon_id, actor, json.dumps({"user_id": str(target["id"]),
                                      "login": target["login"]}, ensure_ascii=False),
    )
    return {"ok": True}


async def change_password(
    pool: Any, *, login: str, old_password: str, new_password: str,
    actor: str = "",
) -> dict[str, Any]:
    """Смена собственного пароля: старый обязателен, логин — только из сессии.

    Без требования старого пароля открытая в браузере вкладка позволяла бы
    сменить пароль зряче — форма приходит от актора, а не от жертвы.
    ``login`` берётся в web.actions из сессии, поэтому «чужой» пароль
    сменить невозможно в принципе: действие не принимает user_id/login из формы.
    """
    login = (login or "").strip().lower()
    if len(str(new_password or "")) < 8:
        return {
            "ok": False, "error": "weak_password",
            "message": "Новый пароль — минимум 8 символов",
        }
    row = await pool.fetchrow(
        "SELECT id, salon_id, password_hash FROM panel_users "
        "WHERE login = $1 AND is_active",
        login,
    )
    if not row or not verify_password(old_password or "", row["password_hash"]):
        return {
            "ok": False, "error": "bad_old_password",
            "message": "Текущий пароль указан неверно",
        }
    await pool.execute(
        "UPDATE panel_users SET password_hash = $2 WHERE id = $1",
        row["id"], hash_password(new_password),
    )
    await pool.execute(
        "INSERT INTO audit_log (salon_id, actor, action, details) "
        "VALUES ($1, $2, 'change_panel_password', $3)",
        row["salon_id"], actor or login,
        json.dumps({"login": login}, ensure_ascii=False),
    )
    return {"ok": True}


async def reset_password(
    pool: Any, *, login: str, actor: str, salon_id: str | None,
    actor_role: str = "owner",
) -> dict[str, Any]:
    """Сброс пароля сотруднику: новый случайный, показывается один раз.

    Изоляция — через ``find_user_in_salon`` (тот же предикат, что в set_totp):
    владелец точки не сбросит пароль чужому сотруднику, а роль ``team``
    работает по всем салонам и служебным учёткам.
    """
    target = await find_user_in_salon(
        pool, login, salon_id=salon_id, actor_role=actor_role,
    )
    if not target:
        return {
            "ok": False, "error": "not_found",
            "message": "Сотрудник не найден в вашей точке",
        }
    password = random_password()
    row = await pool.fetchrow(
        "UPDATE panel_users SET password_hash = $2 WHERE id = $1 AND is_active "
        "RETURNING salon_id",
        target["id"], hash_password(password),
    )
    if not row:
        return {
            "ok": False, "error": "not_found", "message": "Учётка уже отключена",
        }
    await pool.execute(
        "INSERT INTO audit_log (salon_id, actor, action, details) "
        "VALUES ($1, $2, 'reset_panel_password', $3)",
        row["salon_id"], actor,
        json.dumps({"login": target["login"]}, ensure_ascii=False),
    )
    return {"ok": True, "login": target["login"], "password": password}


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
