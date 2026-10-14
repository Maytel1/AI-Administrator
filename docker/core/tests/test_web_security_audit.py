"""Глубокий аудит безопасности панели: изоляция тенанта, целостность аудита,
устойчивость к DoS и корректность транспорта.

Проверки написаны по результатам разбора кода 02.10.2026: каждая закрывает
конкретный класс атак, а не «что-то про безопасность».
"""

import json
from datetime import datetime, timedelta, timezone

from admin_core.web.security import safe_redirect

XSS = "<script>alert(1)</script>"
SQLI = "'; DROP TABLE panel_users; --"

SALON = {
    "id": "11111111-1111-1111-1111-111111111111", "name": "Салон", "plan": "trial",
    "crm_provider": "internal", "timezone": "Asia/Yekaterinburg",
}


class _Req:
    """Минимальный запрос для проверок транспорта (как в test_web_security)."""

    def __init__(self, *, secure: bool = False, headers: dict | None = None,
                 host: str = "panel.example.ru", path: str = "/today"):
        self.secure = secure
        self.headers = headers or {}
        self.host = host
        self.raw_path = path


def _apply(pool, salon_id, form, actor="owner:boss@test.ru"):
    from admin_core.web.actions import apply_form

    return apply_form(pool, salon_id, form, actor=actor)


async def _user_in(pool, *, login, role="owner", salon_id=None, totp=False):
    """Заводит пользователя панели напрямую — как это делает CLI или другой салон."""
    from admin_core.web import users
    from admin_core.web.auth import generate_totp_secret

    return await users.create_user(
        pool, login=login, password="parol-12345", role=role, salon_id=salon_id,
        totp_secret=generate_totp_secret() if totp else None, totp_enabled=totp,
    )


async def _other_salon(pool, name: str):
    return await pool.fetchrow(
        "INSERT INTO salons (name, timezone) VALUES ($1, 'Asia/Yekaterinburg') RETURNING id",
        name,
    )


# == 1. ИЗОЛЯЦИЯ ТЕНАНТА В УПРАВЛЕНИИ ЛЮДЬМИ =============================
# Секрет TOTP и право отключить сотрудника — самое чувствительное, что есть в
# панели. И то и другое должно быть ограничено салоном вызывающего.


async def test_totp_cannot_be_enabled_for_another_salon(demo, pool):
    """Владелец точки А не включает 2FA сотруднику точки Б и не видит его секрет."""
    other = await _other_salon(pool, "audit-other-1")
    try:
        login = "victim@other.ru"
        await _user_in(pool, login=login, salon_id=str(other["id"]))
        result = await _apply(
            pool, str(demo["salon"]["id"]), {"action": "user_totp", "login": login}
        )
        assert result["ok"] is False, "чужой салон нельзя править из панели"
        assert result.get("once") is None, "секрет 2FA чужого салона утекать не должен"
        row = await pool.fetchrow(
            "SELECT totp_enabled, totp_secret FROM panel_users WHERE login = $1", login
        )
        assert row["totp_enabled"] is False
        assert row["totp_secret"] is None
    finally:
        await pool.execute("DELETE FROM panel_users WHERE login = $1", "victim@other.ru")
        await pool.execute("DELETE FROM salons WHERE id = $1", other["id"])


async def test_password_reset_cannot_cross_salons(demo, pool):
    """Сброс пароля чужого сотрудника невозможен: хеш не меняется, пароля не выдаётся."""
    other = await _other_salon(pool, "audit-other-pwd")
    login = "victim-pwd@other.ru"
    try:
        await _user_in(pool, login=login, salon_id=str(other["id"]))
        before = await pool.fetchval(
            "SELECT password_hash FROM panel_users WHERE login = $1", login
        )
        result = await _apply(
            pool, str(demo["salon"]["id"]), {"action": "password_reset", "login": login}
        )
        assert result["ok"] is False, "чужой салон нельзя сбросить из панели"
        assert result.get("once") is None, "новый пароль чужого салона утекать не должен"
        after = await pool.fetchval(
            "SELECT password_hash FROM panel_users WHERE login = $1", login
        )
        assert after == before, "хеш чужого сотрудника не должен меняться"
    finally:
        await pool.execute("DELETE FROM panel_users WHERE login = $1", login)
        await pool.execute("DELETE FROM salons WHERE id = $1", other["id"])


async def test_totp_cannot_be_enabled_for_team_account_by_owner(demo, pool):
    """Учётка нашей команды (salon_id IS NULL) не трогается владельцем точки."""
    login = "hq-team@dz24.ru"
    await _user_in(pool, login=login, role="team")
    try:
        result = await _apply(
            pool, str(demo["salon"]["id"]), {"action": "user_totp", "login": login}
        )
        assert result["ok"] is False
        assert await pool.fetchval(
            "SELECT totp_enabled FROM panel_users WHERE login = $1", login
        ) is False
    finally:
        await pool.execute("DELETE FROM panel_users WHERE login = $1", login)


async def test_team_can_enable_totp_for_own_salon(demo, pool):
    """Наша команда — надстройка над всеми точками: ей можно."""
    login = f"mfa-{str(demo['salon']['id'])[:8]}@test.ru"
    await _user_in(pool, login=login, salon_id=str(demo["salon"]["id"]))
    try:
        result = await _apply(
            pool, str(demo["salon"]["id"]), {"action": "user_totp", "login": login},
            actor="admin:hq@dz24.ru",
        )
        assert result["ok"] is True
        assert len(result["once"]["secret"]) >= 16
        assert await pool.fetchval(
            "SELECT totp_enabled FROM panel_users WHERE login = $1", login
        ) is True
    finally:
        await pool.execute("DELETE FROM panel_users WHERE login = $1", login)


async def test_disable_user_cannot_cross_salons(demo, pool):
    """Нельзя отключить сотрудника чужой точки, даже если в своей есть второй владелец."""
    other = await _other_salon(pool, "audit-other-2")
    login = f"boss-{str(demo['salon']['id'])[:8]}@test.ru"
    try:
        await _user_in(pool, login=login, salon_id=str(other["id"]))
        victim = await pool.fetchrow("SELECT id FROM panel_users WHERE login = $1", login)
        # в своей точке создаём второго владельца, чтобы проверка «последнего» прошла
        await _user_in(pool, login=f"second-{login}", salon_id=str(demo["salon"]["id"]))
        try:
            result = await _apply(
                pool, str(demo["salon"]["id"]),
                {"action": "user_disable", "user_id": str(victim["id"])},
            )
            assert result["ok"] is False
            assert await pool.fetchval(
                "SELECT is_active FROM panel_users WHERE id = $1", victim["id"]
            ) is True
        finally:
            await pool.execute("DELETE FROM panel_users WHERE login = $1", f"second-{login}")
    finally:
        await pool.execute("DELETE FROM panel_users WHERE login = $1", login)
        await pool.execute("DELETE FROM salons WHERE id = $1", other["id"])


async def test_cannot_disable_yourself(demo, pool):
    """Нельзя отключить собственную учётку — иначе можно случайно закрыть себе доступ."""
    salon_id = str(demo["salon"]["id"])
    login = f"self-{salon_id[:8]}@test.ru"
    await _user_in(pool, login=login, salon_id=salon_id)
    me = await pool.fetchrow("SELECT id FROM panel_users WHERE login = $1", login)
    try:
        result = await _apply(
            pool, salon_id, {"action": "user_disable", "user_id": str(me["id"])},
            actor=f"owner:{login}",
        )
        assert result["ok"] is False
        assert result["error"] == "self_disable"
        assert await pool.fetchval(
            "SELECT is_active FROM panel_users WHERE id = $1", me["id"]
        ) is True
    finally:
        await pool.execute("DELETE FROM panel_users WHERE login = $1", login)


async def test_last_owner_of_own_salon_is_protected(demo, pool):
    """Последнего владельца своей точки отключить нельзя — панель не должна осиротеть."""
    salon_id = str(demo["salon"]["id"])
    login = f"last-{salon_id[:8]}@test.ru"
    await _user_in(pool, login=login, salon_id=salon_id)
    me = await pool.fetchrow("SELECT id FROM panel_users WHERE login = $1", login)
    try:
        result = await _apply(
            pool, salon_id, {"action": "user_disable", "user_id": str(me["id"])},
            actor="owner:kollega@test.ru",
        )
        assert result["ok"] is False
        assert result["error"] == "last_owner"
    finally:
        await pool.execute("DELETE FROM panel_users WHERE login = $1", login)

# == 2. УСТОЙЧИВОСТЬ К ИСЧЕРПАНИЮ ПАМЯТИ (DoS) =============================
# Неограниченный счётчик неудачных входов — тоже уязвимость: ключи приходят
# извне, значит их может быть сколько угодно.


def test_throttle_does_not_grow_unbounded():
    """10 тысяч разных «логинов» не превращаются в 10 тысяч ключей в памяти."""
    from admin_core.web.session import Throttle

    t = Throttle(limit=5, window=600.0)
    for i in range(10_000):
        t.fail(f"login:attacker-{i}@test.ru")
    assert len(t._fails) <= Throttle.MAX_KEYS


def test_throttle_prunes_expired_keys():
    """Протухшие ключи вычищаются при обращении, а не копятся вечно."""
    from admin_core.web.session import Throttle

    clock = [0.0]
    t = Throttle(limit=5, window=10.0, clock=lambda: clock[0])
    t.fail("login:a")
    t.fail("login:b")
    clock[0] = 3600.0
    t.prune()
    assert "login:a" not in t._fails and "login:b" not in t._fails


async def test_expired_sessions_are_purged(demo, pool):
    """Сессии не копятся: истёкшие удаляются, живые остаются."""
    from admin_core.web.session import Panel, Session

    panel = Panel(pool=pool, password="")
    now = datetime.now(timezone.utc)
    panel.sessions["live"] = Session(None, "owner", "a@test.ru", None, now + timedelta(hours=1))
    panel.sessions["dead"] = Session(None, "owner", "b@test.ru", None, now - timedelta(minutes=1))
    panel.purge_expired_sessions()
    assert "live" in panel.sessions
    assert "dead" not in panel.sessions


# == 3. ТРАНСПОРТ: РЕДИРЕКТ И ПОВЕДЕНИЕ ЗАГОЛОВКОВ =========================


def test_https_redirect_ignores_forged_host():
    """Host из заголовка не должен уводить браузер на чужой домен."""
    from admin_core.web.security import https_redirect_target

    forged = _Req(secure=False, headers={"Host": "evil.example/phish"}, host="evil.example")
    target = https_redirect_target(forged, tls_enabled=True)
    assert target is None or "evil.example" not in target
    tricky = _Req(secure=False, headers={"Host": "panel.example.ru@evil.example"},
                  host="panel.example.ru@evil.example")
    target2 = https_redirect_target(tricky, tls_enabled=True)
    assert target2 is None or "@" not in target2


def test_https_redirect_uses_allowed_host_when_configured():
    """С явным WEB_PUBLIC_HOST редирект ведёт только на разрешённый домен."""
    from admin_core.web.security import https_redirect_target

    req = _Req(secure=False, headers={"Host": "evil.example"}, host="evil.example")
    target = https_redirect_target(
        req, tls_enabled=True, allowed_hosts=("panel.example.ru",)
    )
    assert target == "https://panel.example.ru/today"


def test_safe_redirect_knows_reports_section():
    """Раздел Отчёты тоже свой — редирект после правки обязан туда вернуть."""
    assert safe_redirect("/reports") == "/reports"
    assert safe_redirect("//evil.example") == "/today"
    assert safe_redirect("/reports?salon=x") == "/reports?salon=x"


def test_csrf_token_is_bound_to_session():
    """Токен из одной сессии не подходит другой — иначе CSRF-токен бесполезен."""
    from admin_core.web.session import Panel

    panel = Panel(pool=None, password="")
    first, second = "session-one", "session-two"
    token_a = panel.csrf_token(first)
    token_b = panel.csrf_token(second)
    assert token_a != token_b
    assert panel.csrf_ok(first, token_a) is True
    assert panel.csrf_ok(first, "wrong") is False
    assert panel.csrf_ok(second, token_a) is False


async def test_session_tokens_are_unique_and_long(demo, pool):
    """Каждый вход выдаёт новый токен: перехват сессии не продлевает доступ."""
    from admin_core.web.session import Panel

    salon_id = str(demo["salon"]["id"])
    login = f"tok-{salon_id[:8]}@test.ru"
    await _user_in(pool, login=login, salon_id=salon_id)
    try:
        panel = Panel(pool=pool, password="")
        tokens = {
            (await panel.login_user(login, "parol-12345"))[0] for _ in range(5)
        }
        assert len(tokens) == 5
        assert all(len(t) >= 32 for t in tokens)
    finally:
        await pool.execute("DELETE FROM panel_users WHERE login = $1", login)

# == 4. ВХОД: НИКАКОГО ОБХОДА ЧЕРЕЗ ЗАПАСНОЙ ПАРОЛЬ =======================


async def test_legacy_password_ignored_when_accounts_exist(demo, pool):
    """Запасной WEB_PANEL_PASSWORD не пускает в панель при живых учётках.

    Иначе любой, кто знает демо-пароль, входит как владелец первой точки.
    """
    from admin_core.web.session import Panel

    login = f"real-{str(demo['salon']['id'])[:8]}@test.ru"
    await _user_in(pool, login=login, salon_id=str(demo["salon"]["id"]))
    try:
        panel = Panel(pool=pool, password="demo-password-123")
        token, error = await panel.login_user("hacker@nowhere.ru", "demo-password-123")
        assert token == "", "чужой e-mail с демо-паролем не должен давать сессию"
        assert error == "invalid"
    finally:
        await pool.execute("DELETE FROM panel_users WHERE login = $1", login)


async def test_login_does_not_reveal_user_existence(demo, pool):
    """Ошибка входа одинакова для «нет пользователя» и «неверный пароль»."""
    from admin_core.web import users

    login = f"timing-{str(demo['salon']['id'])[:8]}@test.ru"
    await _user_in(pool, login=login, salon_id=str(demo["salon"]["id"]))
    try:
        unknown = await users.authenticate(pool, "nobody@nowhere.ru", "parol-12345")
        wrong = await users.authenticate(pool, login, "wrong-password")
        assert unknown["message"] == wrong["message"] == "Неверный логин или пароль"
    finally:
        await pool.execute("DELETE FROM panel_users WHERE login = $1", login)


async def test_login_checks_password_even_for_unknown_user(demo, pool):
    """Даже для несуществующего логина выполняется хеширование.

    Иначе время ответа отличается на десятки миллисекунд — этого хватает,
    чтобы перебором узнать, какие e-mail вообще есть в системе.
    """
    from admin_core.web import users

    calls: list[str] = []
    original = users.verify_password

    def counting(password: str, stored: str) -> bool:
        calls.append(stored)
        return original(password, stored)

    users.verify_password = counting  # noqa: SLF001 — подмена ради измерения
    try:
        await users.authenticate(pool, "nobody@nowhere.ru", "parol-12345")
    finally:
        users.verify_password = original  # noqa: SLF001
    assert calls, "для неизвестного логина хеш всё равно должен считаться"


# == 5. ЦЕЛОСТНОСТЬ АУДИТА, XSS, SQLi И IDOR ==============================


async def test_audit_details_stay_valid_json(demo, pool):
    """Логин с кавычками не должен ломать JSON в audit_log."""
    login = f'quo"te-{str(demo["salon"]["id"])[:8]}@test.ru'
    await _user_in(pool, login=login, salon_id=str(demo["salon"]["id"]))
    try:
        rows = await pool.fetch(
            "SELECT details FROM audit_log WHERE details::text LIKE '%' || $1 || '%'", login
        )
        for row in rows:
            json.loads(row["details"])
    finally:
        await pool.execute("DELETE FROM panel_users WHERE login = $1", login)


async def test_user_actions_are_logged_with_salon(demo, pool):
    """Включение 2FA должно попасть в аудит с указанием точки."""
    login = f"aud-{str(demo['salon']['id'])[:8]}@test.ru"
    await _user_in(pool, login=login, salon_id=str(demo["salon"]["id"]))
    try:
        await _apply(pool, str(demo["salon"]["id"]), {"action": "user_totp", "login": login})
        row = await pool.fetchrow(
            "SELECT salon_id FROM audit_log WHERE action = 'set_panel_totp' "
            "ORDER BY ts DESC LIMIT 1"
        )
        assert row is not None
        assert str(row["salon_id"]) == str(demo["salon"]["id"])
    finally:
        await pool.execute("DELETE FROM panel_users WHERE login = $1", login)


async def test_xss_in_catalog_fields_is_escaped(demo, pool):
    """Название услуги с тегом не исполняется: текст экранируется при показе."""
    from admin_core.web.pages_lists import page_services

    salon_id = str(demo["salon"]["id"])
    result = await _apply(
        pool, salon_id,
        {"action": "service_create", "title": XSS, "price": "500", "duration_min": "30"},
    )
    assert result["ok"] is True
    rows = await pool.fetch(
        "SELECT id, title, duration_min, price, is_active FROM services WHERE salon_id = $1",
        salon_id,
    )
    html = page_services(
        [dict(r) for r in rows],
        {"id": salon_id, "name": "Салон", "timezone": "Asia/Yekaterinburg"},
        [SALON],
    )
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


async def test_sqli_in_new_catalog_fields_is_inert(demo, pool):
    """Инъекция в новые поля каталога не меняет структуру БД."""
    salon_id = str(demo["salon"]["id"])
    result = await _apply(
        pool, salon_id,
        {"action": "service_create", "title": SQLI, "price": "500", "duration_min": "30"},
    )
    assert result["ok"] is True
    assert await pool.fetchval("SELECT count(*) FROM panel_users") >= 0
    assert await pool.fetchval("SELECT count(*) FROM services WHERE title = $1", SQLI) == 1


async def test_archive_of_foreign_entity_is_refused(demo, pool):
    """Чужую услугу из своей точки не отключить (IDOR)."""
    other = await _other_salon(pool, "audit-other-3")
    try:
        svc = await pool.fetchrow(
            "INSERT INTO services (salon_id, title, duration_min, price) "
            "VALUES ($1, 'Чужая', 30, 100) RETURNING id",
            other["id"],
        )
        result = await _apply(
            pool, str(demo["salon"]["id"]),
            {"action": "service_archive", "service_id": str(svc["id"]), "active": "0"},
        )
        assert result["ok"] is False
        assert await pool.fetchval(
            "SELECT is_active FROM services WHERE id = $1", svc["id"]
        ) is True
    finally:
        await pool.execute("DELETE FROM salons WHERE id = $1", other["id"])


def test_actor_role_is_taken_from_session_actor():
    """Роль актора («owner:login») разбирается надёжно: мусор не даст лишних прав."""
    from admin_core.web.actions import _actor_role

    assert _actor_role("owner:boss@test.ru") == "owner"
    assert _actor_role("team:admin@dz24.ru") == "team"
    assert _actor_role("owner") == "guest"   # без логина — не наша роль
    assert _actor_role("admin:hq@dz24.ru") == "team"  # audit_actor так пишет team
    assert _actor_role("") == "guest"
    assert _actor_role("hacker:owner:x") == "guest"


if __name__ == "__main__":  # pragma: no cover
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
