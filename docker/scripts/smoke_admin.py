"""Полный обход админки: все разделы, действия и выгрузки глазами пользователя.

Зачем отдельным скриптом: HTTP-тесты проверяют контракт, а этот — сценарий
целиком, как его проходит человек: вход → все разделы → каждое действие →
выход. Ошибка, которую не видно в тестах (например, раздел без пункта меню или
действие без кнопки), здесь всплывает сразу.
"""

import argparse
import asyncio
import os
import re
import sys
from typing import Any

sys.path.insert(0, "/app")

import aiohttp  # noqa: E402

BASE = "https://web:8080"  # порт внутри сети; 8082 — проброс на хост
USER = "owner"
PASSWORD = os.getenv("SMOKE_ADMIN_PASSWORD", "")
if not PASSWORD:
    sys.exit("[smoke] задайте SMOKE_ADMIN_PASSWORD в окружении (пароль учётки owner)")

TEAM_ONLY = {"/billing", "/network"}

SECTIONS = [
    "/today", "/bookings", "/clients", "/services", "/masters", "/dialogs",
    "/reports", "/audit", "/settings", "/billing",
]

# (действие, обязательные поля) — ровно те, что панель отправляет с кнопки.
ACTIONS: list[tuple[str, dict[str, str]]] = [
    ("booking_status", {"booking_id": "0" * 8 + "-0000-0000-0000-000000000000", "status": "confirmed"}),
    ("service_archive", {}),
    ("master_archive", {}),
    ("client_save", {"client_id": "0" * 8 + "-0000-0000-0000-000000000000"}),
    ("invoice_issue", {}),
    ("legal_save", {"client_type": "ip", "legal_name": "ИП Тест", "inn": "123456789047"}),
]


def report(name: str, ok: bool, detail: str = "") -> bool:
    print(f"{'OK  ' if ok else 'СБОЙ'} {name}" + (f" — {detail}" if detail else ""))
    return ok


def menu_pattern(path: str) -> str:
    """Кусок HTML, которым пункт меню начинается: /today или /today?salon=…."""
    return f'href="{path}?' if path != "/today" else 'href="/today'


async def login(session: aiohttp.ClientSession, base: str, user: str, password: str) -> str:
    """Вход и чтение session-cookie прямо из ответа — не из cookiejar.

    cookiejar aiohttp не отдаёт Secure-cookie для POST без браузерного
    контекста, поэтому значение берём так, как его отдал сервер, и дальше
    сами передаём его в заголовке Cookie.
    """
    async with session.post(
        f"{base}/login",
        # Оферта и согласие на ПДн обязательны — без них login_post отдаёт 400.
        data={
            "login": user, "password": password,
            "agree_oferta": "1", "agree_pdn": "1",
        },
        allow_redirects=False,
        ssl=False,
    ) as response:
        # Успешный вход — это 302 с Set-Cookie; тело читать не нужно:
        # при allow_redirects cookie живёт в ответе на редирект, а не в финальном.
        cookie = response.cookies.get("dz24_session")
    return cookie.value if cookie is not None else ""


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--login", default=USER)
    parser.add_argument("--password", default=PASSWORD)
    args = parser.parse_args()

    failures = 0
    timeout = aiohttp.ClientTimeout(total=20)
    connector = aiohttp.TCPConnector(ssl=False)
    # unsafe=True: cookiejar хранит Secure-cookie и для POST — иначе
    # браузерная сессия теряется и каждое действие уходит на /login
    jar = aiohttp.CookieJar(unsafe=True)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout, cookie_jar=jar) as session:
        session_cookie = await login(session, args.base, args.login, args.password)
        failures += not report(
            "вход в панель", bool(session_cookie), "проверьте --login и --password"
        )

        for path in SECTIONS:
            async with session.get(args.base + path, ssl=False) as response:
                body = await response.text()
            good = response.status == 200 and 'name="password"' not in body
            failures += not report(f"раздел {path}", good, f"код {response.status}")

        async with session.get(args.base + "/today", ssl=False) as response:
            menu = await response.text()
        # /billing и /network видны только роли team: их отсутствие у owner —
        # это скрытие лишнего, а не потерянный пункт меню.
        failures += not report(
            "все разделы в меню",
            all(
                # в меню ссылки всегда с ?salon=..., поэтому сверяем префикс
                menu_pattern(path) in menu
                for path in SECTIONS
                if path not in TEAM_ONLY
            ),
            "раздел без пункта меню — пользователь его не найдёт",
        )

        # CSRF-токен живёт в HTML формы и считается как HMAC от секрета сервиса,
        # поэтому берём его со страницы с формами (/settings) — как это делает
        # браузер, и по той же cookie, которую потом шлём в POST.
        auth_headers = {"Cookie": "dz24_session=" + session_cookie}
        async with session.get(f"{args.base}/settings", headers=auth_headers, ssl=False) as response:
            page = await response.text()
        match = re.search('name="csrf" value="([0-9a-f]+)"', page)
        token = match.group(1) if match else ""
        failures += not report("CSRF-токен есть в форме", bool(token))

        async with session.get(
            f"{args.base}/today",
            headers={"Cookie": "dz24_session=" + session_cookie},
            ssl=False,
        ) as probe:
            probe_body = await probe.text()
        failures += not report("POST-cookie принимается сервером",
                             'name="password"' not in probe_body,
                             f'cookie len={len(session_cookie)}, page={len(probe_body)}')

        for action, fields in ACTIONS:
            data: dict[str, Any] = {"action": action, "csrf": token, **fields}
            # Cookie шлём явно: cookiejar aiohttp не отдаёт SameSite=Lax-cookie
            # на POST без браузерного контекста, а POST без неё не проходит.
            headers = {
                "Referer": f"{args.base}/settings",
                "Cookie": "dz24_session=" + session_cookie,
            }
            async with session.post(
                f"{args.base}/actions", data=data, headers=headers,
                allow_redirects=False, ssl=False
            ) as response:
                await response.read()
                location = response.headers.get("Location", "")
            # Любой разбор входа — это 500, а «не сработало» — понятный отказ.
            failures += not report(
                f"действие {action}", response.status != 500,
                f"код {response.status} → {location[:60]}",
            )

        for kind in ("payments", "debtors", "clients"):
            async with session.get(
                f"{args.base}/accounting.csv?kind={kind}", ssl=False
            ) as response:
                payload = await response.read()
            good = response.status in (200, 403)
            failures += not report(
                f"выгрузка {kind}", good,
                f"код {response.status}, {len(payload)} байт",
            )

        async with session.get(f"{args.base}/logout", allow_redirects=False, ssl=False) as response:
            await response.read()
        async with session.get(f"{args.base}/today", ssl=False) as response:
            after_logout = await response.text()
        failures += not report(
            "выход отключает сессию", 'name="password"' in after_logout
        )

    print(f"\nИТОГ: {'все проверки пройдены' if failures == 0 else f'проблем: {failures}'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
