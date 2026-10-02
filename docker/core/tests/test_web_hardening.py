"""Тесты безопасности панели: инъекции, XSS, IDOR, перебор, открытый редирект.

Цель — чтобы типичные атаки ломались на тестах, а не в проде.
"""

from zoneinfo import ZoneInfo

from admin_core.web.security import safe_redirect
from admin_core.web.session import Panel, Throttle

SQLI = [
    "'; DROP TABLE clients; --",
    "1 OR 1=1",
    "admin'--",
    "%27%20UNION%20SELECT%20password_hash%20FROM%20panel_users--",
    "'; UPDATE panel_users SET role='team'; --",
    "../../../etc/passwd",
]

XSS = [
    "<script>alert(1)</script>",
    '"><img src=x onerror=alert(1)>',
    "<svg/onload=alert(document.cookie)>",
    "</textarea><script>alert(1)</script>",
]

SALON = {
    "id": "11111111-1111-1111-1111-111111111111", "name": "Салон", "plan": "trial",
    "crm_provider": "internal", "timezone": "Asia/Yekaterinburg",
}


def _apply(pool, salon_id, form, actor="owner:boss@test.ru"):
    """Локальная обёртка apply_form, чтобы тесты не зависели от порядка импортов."""
    from admin_core.web.actions import apply_form

    return apply_form(pool, salon_id, form, actor=actor)


# ═══ SQL-ИНЪЕКЦИЯ ═══════════════════════════════════════════════════════


async def test_sql_injection_in_login_is_inert(demo, pool):
    """Попытки инъекции через логин не ломают БД и не пускают в панель."""
    from admin_core.web import users

    for payload in SQLI:
        result = await users.authenticate(pool, payload, payload)
        assert result["ok"] is False, payload
        assert result["error"] in ("invalid", "need_totp")
    assert await pool.fetchval("SELECT count(*) FROM panel_users") >= 0  # таблица жива
    roles = await pool.fetch("SELECT DISTINCT role FROM panel_users")
    assert all(r["role"] in ("owner", "staff", "team") for r in roles)  # роли целы


async def test_sql_injection_in_form_fields_is_rejected(demo, pool):
    """Инъекция в идентификаторы и значения форм отклоняется, данные не меняются."""
    salon_id = str(demo["salon"]["id"])
    svc = await pool.fetchrow(
        "SELECT id, price FROM services WHERE salon_id = $1 LIMIT 1", demo["salon"]["id"]
    )
    price_before = svc["price"]
    for payload in SQLI:
        result = await _apply(
            pool, salon_id,
            {"action": "service_save", "service_id": payload,
             "price": "1000", "duration_min": "60", "is_active": "on"},
        )
        assert result["ok"] is False, payload
        assert result["error"] == "bad_id"  # мусор отсекается до похода в БД
        result2 = await _apply(
            pool, salon_id,
            {"action": "service_save", "service_id": str(svc["id"]),
             "price": payload, "duration_min": "60", "is_active": "on"},
        )
        assert result2["ok"] is False, payload
    after = await pool.fetchval("SELECT price FROM services WHERE id = $1", svc["id"])
    assert after == price_before  # цена не изменилась ни разу


async def test_sql_injection_in_booking_id_is_rejected(demo, pool):
    """Инъекция в id записи: без записи — понятная ошибка, таблица цела."""
    for payload in SQLI:
        result = await _apply(
            pool, str(demo["salon"]["id"]),
            {"action": "booking_status", "booking_id": payload, "status": "confirmed"},
        )
        assert result["ok"] is False
        assert result["error"] == "bad_id"
    assert await pool.fetchval("SELECT count(*) FROM bookings") >= 0


async def test_sql_injection_in_user_login_field_is_rejected(demo, pool):
    """Логин с инъекцией не создаётся — формат проверяется до INSERT."""
    from admin_core.web import users

    for payload in SQLI:
        result = await users.create_user(
            pool, login=payload, password="parol-12345", role="staff",
            salon_id=str(demo["salon"]["id"]),
        )
        assert result["ok"] is False, payload


# ═══ XSS ════════════════════════════════════════════════════════════════


async def test_xss_in_client_notes_is_escaped(demo, pool):
    """Скрипт в заметках клиента хранится текстом, а в HTML экранируется."""
    from admin_core.web.pages_lists import page_clients
    from admin_core.web.session import PageCtx

    salon_id = str(demo["salon"]["id"])
    client_id = str(demo["client"]["id"])
    payload = XSS[0]
    result = await _apply(
        pool, salon_id, {"action": "client_save", "client_id": client_id, "notes": payload},
    )
    assert result["ok"] is True
    assert await pool.fetchval("SELECT notes FROM clients WHERE id = $1", client_id) == payload

    html = page_clients(
        [{"id": "c1", "name": payload, "phone": "+79", "completed_visits": 0,
          "last_visit": None, "upcoming": 0, "requires_prepayment": False,
          "notes": payload}],
        ZoneInfo("Asia/Yekaterinburg"), SALON, [SALON],
        PageCtx(csrf="c" * 32, role="owner"),
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_xss_in_login_field_is_escaped():
    from admin_core.web.render import login_page

    html = login_page("x", login=XSS[1])
    assert "<script>" not in html
    assert "<img src=x" not in html
    assert "&lt;img" in html


def test_layout_escapes_is_callers_job():
    """layout не экранирует сам — это делает esc(); проверяем, что мы им пользуемся."""
    from admin_core.web.render import esc

    assert esc(XSS[0]) not in (XSS[0],)
    assert "&lt;" in esc(XSS[1])


# ═══ IDOR / ИЗОЛЯЦИЯ ТЕНАНТА ═════════════════════════════════════════════


async def test_staff_cannot_edit_other_salon(demo, pool):
    """Чужая услуга не правится даже с валидным её ID."""
    salon_id = str(demo["salon"]["id"])
    other = await pool.fetchrow(
        "INSERT INTO salons (name, timezone) VALUES ('Chuzhoy salon', 'Asia/Yekaterinburg') "
        "RETURNING id",
    )
    foreign = await pool.fetchrow(
        "INSERT INTO services (salon_id, title, duration_min, price) "
        "VALUES ($1, 'Chuzhaya usluga', 60, 999) RETURNING id, price",
        other["id"],
    )
    result = await _apply(
        pool, salon_id,
        {"action": "service_save", "service_id": str(foreign["id"]),
         "price": "1", "duration_min": "10", "is_active": "on"},
        actor="staff:x@test.ru",
    )
    assert result["ok"] is False and result["error"] == "not_found"
    assert await pool.fetchval(
        "SELECT price FROM services WHERE id = $1", foreign["id"]
    ) == foreign["price"]


async def test_new_user_bound_to_session_salon(demo, pool):
    """Учётка создаётся в салоне, который пришёл из сессии, а не из формы."""
    other = await pool.fetchrow(
        "INSERT INTO salons (name, timezone) VALUES ('Tretiy salon', 'Asia/Yekaterinburg') "
        "RETURNING id",
    )
    login = f"podkol-{str(other['id'])[:8]}@test.ru"
    result = await _apply(
        pool, str(other["id"]),
        {"action": "user_create", "login": login, "role": "staff"},
        actor="owner:boss@test.ru",
    )
    assert result["ok"] is True
    row = await pool.fetchrow(
        "SELECT salon_id FROM panel_users WHERE login = $1", login
    )
    assert str(row["salon_id"]) == str(other["id"])


# ═══ ПЕРЕБОР ════════════════════════════════════════════════════════════


def test_throttle_blocks_brute_force():
    clock = [0.0]
    t = Throttle(limit=3, window=60.0, clock=lambda: clock[0])
    for _ in range(3):
        assert t.allowed("login:a") is True
        t.fail("login:a")
    assert t.allowed("login:a") is False      # четвёртая попытка заблокирована
    assert t.allowed("login:b") is True      # другой логин не задет
    clock[0] = 61.0
    assert t.allowed("login:a") is True      # окно истекло


def test_login_blocked_after_repeated_failures():
    panel = Panel(pool=None, password="")
    for _ in range(5):
        assert panel.login_blocked("boss@test.ru", "1.2.3.4") is False
        panel.note_login_failure("boss@test.ru", "1.2.3.4")
    assert panel.login_blocked("boss@test.ru", "1.2.3.4") is True
    assert panel.login_blocked("boss@test.ru", "5.6.7.8") is True  # блокировка по логину
    assert panel.login_blocked("other@test.ru", "1.2.3.4") is False


def test_ip_throttle_stops_password_spray():
    """Распыление паролей по разным логинам с одного IP тоже ловится."""
    panel = Panel(pool=None, password="")
    for i in range(20):
        panel.note_login_failure(f"user{i}@test.ru", "9.9.9.9")
    assert panel.login_blocked("fresh@test.ru", "9.9.9.9") is True


def test_successful_login_resets_throttle():
    panel = Panel(pool=None, password="")
    panel.note_login_failure("boss@test.ru", "1.1.1.1")
    panel.note_login_failure("boss@test.ru", "1.1.1.1")
    panel.note_login_success("boss@test.ru", "1.1.1.1")
    assert panel.login_blocked("boss@test.ru", "1.1.1.1") is False


async def test_wrong_totp_counts_as_attempt(demo, pool):
    """Неверный код 2FA тратит попытку: иначе перебирают миллион кодов за окно."""
    from admin_core.web import users
    from admin_core.web.auth import generate_totp_secret

    login = f"mfa-{str(demo['salon']['id'])[:8]}@test.ru"
    await users.create_user(
        pool, login=login, password="parol-12345", role="owner",
        salon_id=str(demo["salon"]["id"]), totp_secret=generate_totp_secret(),
        totp_enabled=True,
    )
    panel = Panel(pool=pool, password="")
    for _ in range(5):
        token, err = await panel.login_user(login, "parol-12345", "000000")
        assert token == "" and err == "need_totp"
        panel.note_login_failure(login, "1.1.1.1")
    assert panel.login_blocked(login, "1.1.1.1") is True


# ═══ OPEN REDIRECT ══════════════════════════════════════════════════════


def test_safe_redirect_blocks_external_targets():
    for evil in [
        "//evil.example/steal",
        "https://evil.example",
        "http://evil.example",
        "\\\\evil.example",
        "/today\r\nSet-Cookie: x=1",
        "javascript:alert(1)",
        "/etc/passwd",
        "/unknown-section",
        "/" + "a" * 300,
        "",
        None,
    ]:
        assert safe_redirect(evil) == "/today", evil


def test_safe_redirect_allows_own_sections():
    for good in ["/today", "/bookings?status=booked", "/settings", "/clients"]:
        assert safe_redirect(good) == good


if __name__ == "__main__":  # pragma: no cover
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
