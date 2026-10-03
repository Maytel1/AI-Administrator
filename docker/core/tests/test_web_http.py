"""Тесты HTTP-слоя панели: маршруты, авторизация, CSRF, права, редиректы.

Зачем они: ядро покрыто 200+ тестами, а сам `main.py` не был проверен ничем.
Из-за этого раздел «Отчёты» однажды отдавал 404 (маршрут не был зарегистрирован),
а смена сигнатуры страницы едва не уронила бот-панель. Всё это ловится здесь
за секунды, а не глазами через curl.

Приложение поднимается на реальном порту через aiohttp test_utils, БД — та же,
что у остальных тестов (фикстура demo).
"""


import pytest

# HTTP-слой лежит в /app/app: тесты делают ровно import app.main, как сервис.

SECTIONS = [
    "/today", "/bookings", "/clients", "/services", "/masters",
    "/dialogs", "/reports", "/network", "/audit", "/settings",
]
# Признак страницы входа: поле пароля. Проверять слово «Вход» нельзя — оно
# есть и в <title>, из-за чего проверка проходит на любой странице.
LOGIN_MARK = 'name="password"'


@pytest.fixture()
async def client(demo, pool):
    """Поднятая панель с сессией владельца точки."""
    from admin_core.web.session import Panel
    from aiohttp.test_utils import TestClient, TestServer
    from app.main import build_app  # noqa: PLC0415 — путь добавляем выше

    panel = Panel(pool, metrics=None)
    app = build_app(panel)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    token = await panel.open_session(
        role="owner", login="boss@test.ru", salon_id=str(demo["salon"]["id"])
    )
    test_client.session.cookie_jar.update_cookies({"dz24_session": token})
    yield test_client, panel, token, demo
    await test_client.close()


async def test_all_sections_open_for_owner(client):
    """Каждый раздел панели отвечает 200 — ровно то, что сломалось с /reports."""
    http, _panel, _token, _demo = client
    for path in SECTIONS:
        response = await http.get(path)
        assert response.status == 200, f"{path} -> {response.status}"
        assert LOGIN_MARK not in await response.text(), f"{path} показал форму входа"


async def test_anonymous_gets_login_form(client):
    """Без сессии разделы отдают форму входа, а не данные."""
    http, _panel, token, _demo = client
    http.session.cookie_jar.clear()
    response = await http.get("/today")
    assert response.status == 200
    assert LOGIN_MARK in await response.text()
    response = await http.post(
        "/actions", data={"action": "booking_status"},
        allow_redirects=False,
    )
    assert response.status in (302, 303)
    assert "/login" in response.headers.get("Location", "")


async def test_actions_require_csrf(client):
    """POST без CSRF-токена отклоняется даже с валидной сессией."""
    http, _panel, _token, demo = client
    response = await http.post(
        "/actions",
        data={
            "action": "booking_status",
            "booking_id": "11111111-1111-1111-1111-111111111111",
            "status": "confirmed",
        },
        allow_redirects=False,
    )
    assert response.status in (302, 303)
    assert "err=1" in response.headers.get("Location", "")


async def test_unknown_action_is_refused(client):
    """Неизвестное действие не должно «проходить»."""
    http, _panel, _token, _demo = client
    csrf = http.session.cookie_jar.filter_cookies("http://127.0.0.1")["dz24_session"].value
    response = await http.post(
        "/actions",
        data={"action": "drop_everything", "csrf": csrf},
        allow_redirects=False,
    )
    assert response.status in (302, 303)
    assert "err=1" in response.headers.get("Location", "")


async def test_export_csv_requires_session_and_serves_bytes(client):
    """Выгрузка отдаётся авторизованному и только как CSV."""
    http, _panel, _token, _demo = client
    response = await http.get("/export.csv?period=30")
    assert response.status == 200
    assert "text/csv" in response.headers.get("Content-Type", "")
    assert response.headers["Cache-Control"] == "no-store"
    assert (await response.read()).startswith(b"\xef\xbb\xbf")
    http.session.cookie_jar.clear()
    assert (await http.get("/export.csv", allow_redirects=False)).status in (302, 303)


async def test_methods_are_restricted(client):
    """Данные меняются только POST: GET /actions не должен ничего делать."""
    http, _panel, _token, _demo = client
    assert (await http.get("/actions")).status == 405
    assert (await http.put("/actions")).status == 405
    assert (await http.delete("/actions")).status == 405


async def test_logout_ends_session(client):
    """Выход действительно завершает сессию, а не просто чистит cookie у браузера."""
    http, panel, token, _demo = client
    assert (await http.get("/logout", allow_redirects=False)).status in (302, 303)
    assert await panel.authorized(token) is False
    response = await http.get("/today")
    assert LOGIN_MARK in await response.text()


async def test_security_headers_on_every_response(client):
    """Заголовки безопасности ставятся на каждый ответ, а не только на форму входа."""
    http, _panel, _token, _demo = client
    for path in ("/today", "/login", "/static.css"):
        headers = (await http.get(path)).headers
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert "default-src" in headers["Content-Security-Policy"]


async def test_owner_cannot_open_network(client):
    """Сводка по сети владельцу не показывается даже при прямом заходе на URL."""
    http, _panel, _token, _demo = client
    response = await http.get("/network")
    assert response.status == 200
    assert "Недостаточно прав" in await response.text()


async def test_billing_page_is_hidden_from_owner(client):
    """Счета и реквизиты видит только наша команда: владелец получает отказ."""
    http, _panel, _token, _demo = client
    response = await http.get("/billing")
    assert response.status == 200
    body = await response.text()
    assert LOGIN_MARK not in body
    assert "Недостаточно прав" in body
    assert "БИК" not in body, "реквизиты не должны утекать владельцу"


async def test_billing_page_opens_for_team(demo, pool):
    """У нашей команды раздел открывается и показывает реквизиты."""
    from aiohttp.test_utils import TestClient, TestServer  # noqa: PLC0415

    from app.main import build_app  # noqa: PLC0415
    from admin_core.web.session import Panel  # noqa: PLC0415

    panel = Panel(pool, metrics=None)
    app = build_app(panel)
    http = TestClient(TestServer(app))
    await http.start_server()
    token = await panel.open_session(
        role="team", login="admin@test.ru", salon_id=None
    )
    http.session.cookie_jar.update_cookies({"dz24_session": token})
    try:
        response = await http.get("/billing")
        assert response.status == 200
        body = await response.text()
        assert LOGIN_MARK not in body
        assert "Недостаточно прав" not in body
        assert "Тариф и оплата" in body
    finally:
        await http.close()


async def test_billing_actions_require_permission(demo, pool):
    """Прямой POST от администратора не должен выставлять счёт."""
    from admin_core.web.roles import can_action  # noqa: PLC0415

    assert can_action("staff", "invoice_issue") is False
    assert can_action("owner", "invoice_issue") is False
    assert can_action("team", "invoice_issue") is True
    assert can_action("team", "payment_mark_paid") is True


async def test_issue_invoice_via_billing_table(demo, pool):
    """Счёт из таблицы платежей попадает в историю панели."""
    from admin_core import billing  # noqa: PLC0415

    await pool.execute(
        "UPDATE salons SET plan = 'standard' WHERE id = $1", demo["salon"]["id"]
    )
    salon = dict(await pool.fetchrow(
        "SELECT id, name, plan, paid_until FROM salons WHERE id = $1", demo["salon"]["id"]
    ))
    invoice = await billing.issue_invoice(pool, salon)
    history = await pool.fetch(
        "SELECT period_start, amount, status FROM payments WHERE salon_id = $1",
        salon["id"],
    )
    assert len(history) == 1
    assert float(history[0]["amount"]) == invoice["amount"]


async def test_overdue_payment_raises_alert(demo, pool):
    """Просроченный счёт должен поднять тревогу, а не молчать."""
    from admin_core.billing import issue_invoice  # noqa: PLC0415
    from admin_core.monitoring import check_alerts  # noqa: PLC0415

    await pool.execute(
        "UPDATE salons SET plan = 'start', is_active = true WHERE id = $1",
        demo["salon"]["id"],
    )
    salon = dict(await pool.fetchrow(
        "SELECT id, name, plan, paid_until FROM salons WHERE id = $1", demo["salon"]["id"]
    ))
    await issue_invoice(pool, salon)
    # Счёт за прошлый месяц: период уже истёк, оплаты нет.
    await pool.execute(
        "UPDATE payments SET period_start = CURRENT_DATE - 40, "
        "period_end = CURRENT_DATE - 10 WHERE salon_id = $1",
        salon["id"],
    )
    alerts = await check_alerts(pool)
    kinds = {alert.kind for alert in alerts}
    assert "payments_overdue" in kinds


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
