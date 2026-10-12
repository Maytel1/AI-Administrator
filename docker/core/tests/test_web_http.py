"""Тесты HTTP-слоя панели: маршруты, авторизация, CSRF, права, редиректы.

Зачем они: ядро покрыто 200+ тестами, а сам `main.py` не был проверен ничем.
Из-за этого раздел «Отчёты» однажды отдавал 404 (маршрут не был зарегистрирован),
а смена сигнатуры страницы едва не уронила бот-панель. Всё это ловится здесь
за секунды, а не глазами через curl.

Приложение поднимается на реальном порту через aiohttp test_utils, БД — та же,
что у остальных тестов (фикстура demo).
"""


import pytest
import yarl

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
    from admin_core.web.session import Panel  # noqa: PLC0415
    from aiohttp.test_utils import TestClient, TestServer  # noqa: PLC0415
    from app.main import build_app  # noqa: PLC0415

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


async def test_pay_page_opens_for_owner(client):
    """/pay — страница салона: владелец видит статус и правит свои реквизиты."""
    http, _panel, _token, _demo = client
    response = await http.get("/pay")
    assert response.status == 200
    body = await response.text()
    assert LOGIN_MARK not in body
    assert "Недостаточно прав" not in body
    assert "Оплата" in body
    assert 'name="client_type"' in body, "владелец должен править карточку плательщика"


async def test_pay_page_is_hidden_from_staff(client, demo, pool):
    """Администратор точки не оплачивает тариф и не видит реквизиты."""
    from admin_core.web.session import Panel  # noqa: PLC0415
    from aiohttp.test_utils import TestClient, TestServer  # noqa: PLC0415
    from app.main import build_app  # noqa: PLC0415

    panel = Panel(pool, metrics=None)
    app = build_app(panel)
    http = TestClient(TestServer(app))
    await http.start_server()
    token = await panel.open_session(
        role="staff", login="recep@test.ru", salon_id=str(demo["salon"]["id"])
    )
    http.session.cookie_jar.update_cookies({"dz24_session": token})
    try:
        response = await http.get("/pay")
        assert response.status == 200
        body = await response.text()
        assert LOGIN_MARK not in body
        assert "Недостаточно прав" in body
        assert "БИК" not in body, "реквизиты не должны утекать администратору"
    finally:
        await http.close()


async def test_pay_page_opens_for_team(demo, pool):
    """/pay у нашей команды открывается: статус тарифа + реквизиты + профиль."""
    from admin_core.web.session import Panel  # noqa: PLC0415
    from aiohttp.test_utils import TestClient, TestServer  # noqa: PLC0415
    from app.main import build_app  # noqa: PLC0415

    panel = Panel(pool, metrics=None)
    app = build_app(panel)
    http = TestClient(TestServer(app))
    await http.start_server()
    token = await panel.open_session(
        role="team", login="admin@test.ru", salon_id=None
    )
    http.session.cookie_jar.update_cookies({"dz24_session": token})
    try:
        response = await http.get("/pay")
        assert response.status == 200
        body = await response.text()
        assert LOGIN_MARK not in body
        assert "Недостаточно прав" not in body
        assert "Оплата" in body
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


async def test_every_section_answers_for_owner(client):
    """Каждый раздел панели открывается владельцу: список берём из кода, не рукой.

    Именно так раньше терялся раздел — он был в коде, но не в меню и не в
    тестах, и обнаруживался только пользователем.
    """
    import ast  # noqa: PLC0415
    import pathlib  # noqa: PLC0415

    # В тестовом образе код веба лежит в /app/app, в репозитории — services/web/app.
    candidates = [
        pathlib.Path(__file__).resolve().parents[2] / "services" / "web" / "app" / "main.py",
        pathlib.Path("/app/app/main.py"),
    ]
    target = next(path for path in candidates if path.exists())
    tree = ast.parse(target.read_text(encoding="utf-8"))
    paths: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "sections":
            paths = [key.value for key in node.value.keys]
    assert len(paths) >= 10, "список разделов должен покрывать всю панель"
    http, _panel, _token, _demo = client
    for path in paths:
        response = await http.get(path)
        assert response.status == 200, f"{path} -> {response.status}"
        assert LOGIN_MARK not in await response.text(), f"{path} показал вход"


async def test_every_action_is_covered_by_permissions(client):
    """Каждое действие формы имеет запись о требуемом праве.

    Действие без права — это либо «доступно всем», либо «недоступно никому»;
    оба варианта ломают панель тихо.
    """
    import ast  # noqa: PLC0415
    import pathlib  # noqa: PLC0415

    from admin_core.web.roles import can_action  # noqa: PLC0415

    core = pathlib.Path(__file__).resolve().parents[1]
    tree = ast.parse((core / "admin_core" / "web" / "actions.py").read_text(encoding="utf-8"))
    actions: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Name)
            and node.left.id == "action"
        ):
            actions.append(node.comparators[0].value)
    assert len(actions) >= 18, "в actions.py должно быть не меньше 18 действий"
    for action in actions:
        assert can_action("team", action) or can_action("owner", action), (
            f"действие {action} недоступно ни одной роли"
        )
    # Опасные действия не должны достаться администратору точки.
    for action in ("user_create", "user_totp", "user_disable", "invoice_issue",
                   "payment_mark_paid", "legal_save"):
        if action in actions:
            assert can_action("staff", action) is False, (
                f"{action} не должно быть доступно администратору"
            )


async def _csrf(http) -> str:  # noqa: ANN001
    """Токен сессии используется как CSRF в тестах HTTP-слоя."""
    return http.session.cookie_jar.filter_cookies(
        yarl.URL("http://127.0.0.1")
    )["dz24_session"].value


# --- Оферта, ПДн и согласия на входе -----------------------------------------

CONSENT = {"agree_oferta": "1", "agree_pdn": "1"}


async def test_oferta_and_pdn_pages_are_public(client):
    """Документы открываются без входа — иначе ссылка на форме логина ведёт в никуда."""
    http, _panel, _token, _demo = client
    http.session.cookie_jar.clear()
    oferta = await http.get("/oferta")
    assert oferta.status == 200
    text = await oferta.text()
    assert "Публичная оферта" in text and "15 000" in text  # тарифы из TARIFFS.md
    pdn = await http.get("/pdn")
    assert pdn.status == 200
    text = await pdn.text()
    assert "152-ФЗ" in text and "Обработка персональных данных" in text


async def test_login_js_is_served(client):
    """CSP запрещает inline-скрипты — страница входа грузит /login.js отдельным файлом."""
    http, _panel, _token, _demo = client
    http.session.cookie_jar.clear()
    response = await http.get("/login.js")
    assert response.status == 200
    assert "remember" in await response.text()


async def test_login_page_shows_consents_and_remember(client):
    """На форме входа — две обязательные галочки согласия и «Запомнить меня»."""
    http, _panel, _token, _demo = client
    http.session.cookie_jar.clear()
    response = await http.get("/login")
    text = await response.text()
    assert 'name="agree_oferta"' in text and 'name="agree_pdn"' in text
    assert 'name="remember"' in text
    assert 'href="/oferta"' in text and 'href="/pdn"' in text


async def test_login_post_without_consents_is_rejected(client):
    """POST без согласий отклоняется сервером, даже с верным паролем: 400 и без cookie."""
    http, _panel, _token, _demo = client
    http.session.cookie_jar.clear()
    response = await http.post(
        "/login",
        data={"login": "nobody@nowhere.ru", "password": "any-password"},
    )
    assert response.status == 400
    assert "примите условия оферты" in (await response.text()).lower()
    assert not http.session.cookie_jar.filter_cookies(
        yarl.URL("http://127.0.0.1")
    ).get("dz24_session")


async def test_login_post_with_consents_and_wrong_password(client):
    """Согласия есть, пароль неверный — входим только с ошибкой, cookie нет."""
    http, _panel, _token, _demo = client
    http.session.cookie_jar.clear()
    response = await http.post(
        "/login",
        data={"login": "nobody@nowhere.ru", "password": "wrong", **CONSENT},
    )
    assert response.status == 200
    assert "Неверный логин или пароль" in await response.text()
    assert not http.session.cookie_jar.filter_cookies(
        yarl.URL("http://127.0.0.1")
    ).get("dz24_session")


async def test_login_post_success_without_remember_sets_session_cookie(client, pool):
    """Вход без «Запомнить меня»: обычный session-cookie (без max-age)."""
    from admin_core.web import users  # noqa: PLC0415

    http, _panel, _token, demo = client
    http.session.cookie_jar.clear()
    await users.create_user(
        pool, login="remember-off@test.ru", password="parol-12345",
        role="staff", salon_id=str(demo["salon"]["id"]),
    )
    response = await http.post(
        "/login",
        data={"login": "remember-off@test.ru", "password": "parol-12345", **CONSENT},
        allow_redirects=False,
    )
    assert response.status == 302
    assert response.headers["Location"] == "/today"
    jar = http.session.cookie_jar.filter_cookies(yarl.URL("http://127.0.0.1"))
    assert "dz24_session" in jar
    assert not jar["dz24_session"]["max-age"]  # session-cookie
    assert "dz24_login" not in jar  # без галочки логин не запоминаем


async def test_login_post_remember_sets_long_cookie_and_login(client, pool):
    """С «Запомнить меня»: cookie живёт 30 дней, логин сохраняется для предзаполнения."""
    from admin_core.web import users  # noqa: PLC0415

    http, _panel, _token, demo = client
    http.session.cookie_jar.clear()
    await users.create_user(
        pool, login="remember-on@test.ru", password="parol-12345",
        role="staff", salon_id=str(demo["salon"]["id"]),
    )
    response = await http.post(
        "/login",
        data={
            "login": "remember-on@test.ru", "password": "parol-12345",
            "remember": "1", **CONSENT,
        },
        allow_redirects=False,
    )
    assert response.status == 302
    # Проверяем заголовок Set-Cookie напрямую: cookie_jar aiohttp не хранит max-age.
    session_headers = [
        h for h in response.headers.getall("Set-Cookie")
        if h.startswith("dz24_session=")
    ]
    assert session_headers and "Max-Age=2592000" in session_headers[0]
    jar = http.session.cookie_jar.filter_cookies(yarl.URL("http://127.0.0.1"))
    assert jar["dz24_login"].value == "remember-on@test.ru"  # только логин!

    # После выхода логин подставляется в поле, пароль — нет.
    await http.get("/logout", allow_redirects=False)
    page = await http.get("/login")
    text = await page.text()
    assert 'value="remember-on@test.ru"' in text


async def test_login_without_js_still_requires_consents(client, pool):
    """Скрытое поле согласия с шага 2FA проходит серверную проверку повторно."""
    from admin_core.web import users  # noqa: PLC0415

    http, _panel, _token, demo = client
    http.session.cookie_jar.clear()
    await users.create_user(
        pool, login="hidden-consent@test.ru", password="parol-12345",
        role="staff", salon_id=str(demo["salon"]["id"]),
    )
    # POST как после шага 2FA: согласия приходят скрытыми полями.
    response = await http.post(
        "/login",
        data={"login": "hidden-consent@test.ru", "password": "parol-12345", **CONSENT},
        allow_redirects=False,
    )
    assert response.status == 302


async def test_pay_card_requires_login(client):
    """POST /pay/card без сессии — уводит на вход, а не создаёт платёж."""
    http, _panel, _token, _demo = client
    http.session.cookie_jar.clear()
    response = await http.post("/pay/card", data={}, allow_redirects=False)
    assert response.status in (302, 303)
    assert "/login" in response.headers.get("Location", "")


async def test_pay_card_requires_csrf(client):
    """POST /pay/card без CSRF отклоняется даже с валидной сессией владельца."""
    http, _panel, _token, _demo = client
    response = await http.post("/pay/card", data={}, allow_redirects=False)
    assert response.status in (302, 303)
    assert "err=1" in response.headers.get("Location", "")


async def test_pay_card_without_keys_redirects_to_manual(client):
    """POST /pay/card без ключей эквайринга — понятный отказ и назад на /pay.

    Сессия и CSRF проходят, но провайдер не настроен (в тестах ключи
    YOOKASSA_* не заданы) → человеку показывается ошибка, а не зависший
    редирект на несуществующую страницу оплаты.
    """
    http, _panel, token, _demo = client
    response = await http.post(
        "/pay/card", data={"csrf": token}, allow_redirects=False,
    )
    assert response.status in (302, 303)
    location = response.headers.get("Location", "")
    assert location.startswith("/pay?") and "err=1" in location


async def test_pay_notify_is_public_and_returns_200(client):
    """Вебхук ЮKassa публичен (без сессии/CSRF) и всегда отвечает 200.

    ЮKassa доставляет уведомления server-to-server, без cookie. 200 на
    не-настроенный эквайринг — чтобы провайдер не ушёл в бесконечный ретрай.
    Логика сверки платежа и суммы — в test_acquiring.py.
    """
    http, _panel, _token, _demo = client
    http.session.cookie_jar.clear()  # вебхук приходит без сессии
    response = await http.post(
        "/pay/notify",
        json={"type": "notification", "event": "payment.succeeded",
              "object": {"id": "pay_test", "status": "succeeded"}},
    )
    assert response.status == 200
    payload = await response.json()
    # Эквайринг в тестах не настроен — ответ not_configured, но код 200.
    assert payload["ok"] is False


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
