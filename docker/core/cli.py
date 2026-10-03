"""CLI-режим диалога (фаза 1, «без мессенджеров» — DEVELOPMENT_PLAN.md).

Запуск из папки docker/:
    docker compose run --rm cli            # диалог в терминале
    docker compose run --rm cli --seed     # демо-данные (идемпотентно)
    docker compose run --rm cli python -m pytest -q

Все сообщения сохраняются в dialogs / dialog_messages (ПДн — только в нашей БД;
в LLM уходит обезличенный текст, admin_core.anonymize).
"""

import argparse
import asyncio

from admin_core.agent.agent import AgentSession
from admin_core.config import get_settings
from admin_core.crm import make_crm
from admin_core.db import get_pool, run_migrations
from admin_core.llm import make_provider
from admin_core.web.auth import provisioning_uri


async def do_seed() -> None:
    pool = await get_pool()
    try:
        await run_migrations(pool)
        from seed import seed as seed_demo  # noqa: PLC0415 — лениво, чтобы не тянуть в тесты

        summary = await seed_demo(pool)
        print(f"[seed] готово: {summary}")
    finally:
        await pool.close()


async def dialog() -> None:
    settings = get_settings()
    pool = await get_pool()
    try:
        await run_migrations(pool)
        async with pool.acquire() as conn:
            if settings.demo_salon_name:
                salon = await conn.fetchrow(
                    "SELECT * FROM salons WHERE name ILIKE $1 AND is_active LIMIT 1",
                    f"%{settings.demo_salon_name}%",
                )
            else:
                salon = await conn.fetchrow(
                    "SELECT * FROM salons WHERE is_active ORDER BY created_at LIMIT 1"
                )
        if not salon:
            print("В БД нет салона. Сначала: docker compose run --rm cli --seed")
            return

        crm = make_crm(pool, "internal")
        client = await crm.ensure_client(
            str(salon["id"]), settings.cli_client_phone or "+79001234567"
        )
        try:
            provider = make_provider(settings)
        except RuntimeError as exc:
            print(f"[config] {exc}")
            print("[config] для демо без ключей поставьте LLM_PROVIDER=fake в docker/.env")
            return

        session = AgentSession(crm, provider, dict(salon), dict(client), created_by="bot:cli")
        dialog_id = await pool.fetchval(
            "INSERT INTO dialogs (salon_id, client_id, channel) VALUES ($1, $2, 'cli') "
            "RETURNING id",
            str(salon["id"]), str(client["id"]),
        )

        print(f"=== Демо-диалог с салоном «{salon['name']}» (провайдер: {provider.name}) ===")
        print("Первая реплика — ваш телефон уже распознан (CLI_CLIENT_PHONE).")
        print("Пишите текст; выход: 'выход' / 'exit' / Ctrl+C")
        profile = await session.crm.get_client_profile(str(salon["id"]), str(client["id"]))
        print("Анна: " + (f"Здравствуйте{', ' + profile['name'] if profile.get('name') else ''}! "
                         f"Салон «{salon['name']}». Чем помочь?"))
        while True:
            try:
                line = input("Вы: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line.lower() in ("выход", "exit", "quit"):
                break
            await pool.execute(
                "INSERT INTO dialog_messages (dialog_id, role, content) VALUES ($1, 'user', $2)",
                dialog_id, line,
            )
            reply = await session.run_turn(line)
            print(f"Анна: {reply}")
            await pool.execute(
                "INSERT INTO dialog_messages (dialog_id, role, content) VALUES ($1, 'assistant', $2)",
                dialog_id, reply,
            )
        await pool.execute(
            "UPDATE dialogs SET ended_at = now(), outcome = $2 WHERE id = $1",
            dialog_id, "dialog_ended",
        )
    finally:
        await pool.close()


async def do_user(args: argparse.Namespace) -> None:
    """Создание учётной записи панели: логин, роль, пароль и TOTP-секрет.

    Пароль и секрет печатаются один раз — сохраните их: в базе только хеш.
    """
    from admin_core.db import get_pool  # noqa: PLC0415
    from admin_core.web import users as web_users  # noqa: PLC0415
    from admin_core.web.auth import generate_totp_secret  # noqa: PLC0415

    pool = await get_pool()
    try:
        await run_migrations(pool)
        salon_id = None
        if args.role != "team":
            row = await pool.fetchrow(
                "SELECT id FROM salons WHERE name ILIKE $1 AND is_active LIMIT 1",
                f"%{args.salon or ''}%",
            )
            if not row:
                print(f"Не найден салон по «{args.salon}». Список:")
                for s in await pool.fetch("SELECT name FROM salons WHERE is_active"):
                    print("  -", s["name"])
                return
            salon_id = str(row["id"])
        secret = generate_totp_secret() if args.totp else None
        result = await web_users.create_user(
            pool, login=args.user, password=args.password, role=args.role,
            salon_id=salon_id, name=args.name, totp_secret=secret,
            totp_enabled=args.totp, actor="system:cli",
        )
        if not result["ok"]:
            print(f"[user] не создано: {result['message']}")
            return
        print(f"[user] создан: {result['login']} (роль {result['role']})")
        if args.totp:
            uri = provisioning_uri(secret or "", args.user)
            print(f"[user] секрет 2FA: {secret}")
            print(f"[user] ссылка для приложения: {uri}")
        print("[user] пароль напечатан один раз и в базе хранится только хеш")
    finally:
        await pool.close()


async def _salon_id(pool, name: str | None) -> str | None:
    """Найти точку по названию (как в --user). None — «не найдена»."""
    if not name:
        return None
    row = await pool.fetchrow(
        "SELECT id FROM salons WHERE name ILIKE $1 AND is_active LIMIT 1", f"%{name}%"
    )
    return str(row["id"]) if row else None


async def do_forget(args: argparse.Namespace) -> None:
    """Удаление данных клиента по запросу (152-ФЗ) — из CLI, а не из панели.

    Из панели это опасно делать одной кнопкой: обезличивание необратимо.
    """
    from admin_core.monitoring import forget_client

    pool = await get_pool()
    try:
        salon_id = await _salon_id(pool, args.salon)
        if not salon_id:
            print(f"[cli] салон «{args.salon}» не найден")
            return
        client = await pool.fetchrow(
            "SELECT id, phone, name FROM clients WHERE salon_id = $1 AND phone LIKE $2",
            salon_id, f"%{args.forget}%",
        )
        if not client:
            print(f"[cli] клиент {args.forget} не найден")
            return
        result = await forget_client(pool, salon_id, str(client["id"]), actor="cli:request")
        if result["ok"]:
            print(f"[cli] данные клиента {client['phone']} удалены (журнал: audit_log)")
        else:
            print(f"[cli] не удалось: {result.get('error')}")
    finally:
        await pool.close()


async def do_onboard(args: argparse.Namespace) -> None:
    """Онбординг точки за одну команду: салон, мастер, услуги, учётка.

    Менеджер не должен собирать точку по пяти разным командам — пока он
    ковыряется, клиент ждёт.
    """
    from admin_core.crm.internal import InternalCRM
    from admin_core.web.users import create_user

    pool = await get_pool()
    try:
        salon = await pool.fetchrow(
            "INSERT INTO salons (name, timezone, plan, crm_provider) "
            "VALUES ($1, $2, $3, 'internal') RETURNING id, name",
            args.name, args.timezone, args.plan,
        )
        salon_id = str(salon["id"])
        crm = InternalCRM(pool)
        master = await pool.fetchrow(
            "INSERT INTO masters (salon_id, name) VALUES ($1, $2) RETURNING id",
            salon_id, args.master,
        )
        for title, price in (("Консультация", "500"), ("Основная услуга", "2000")):
            service = await pool.fetchrow(
                "INSERT INTO services (salon_id, title, duration_min, price) "
                "VALUES ($1, $2, 60, $3) RETURNING id",
                salon_id, title, price,
            )
            await pool.execute(
                "INSERT INTO master_services (master_id, service_id) VALUES ($1, $2)",
                master["id"], service["id"],
            )
        for weekday in range(7):  # работаем без выходных по умолчанию
            await pool.execute(
                "INSERT INTO working_hours (master_id, weekday, start_time, end_time) "
                "VALUES ($1, $2, '10:00', '20:00')",
                master["id"], weekday,
            )
        user = await create_user(
            pool, login=args.login, password=args.password or "parol-12345",
            role="owner", salon_id=salon_id, name=args.master, actor="cli:onboard",
        )
        print(f"[cli] точка «{salon['name']}» создана: {salon_id}")
        print(f"[cli] мастер: {args.master}, услуг: 2, график: ежедневно 10:00–20:00")
        if user["ok"]:
            print(f"[cli] вход в панель: {user['login']} / {args.password or 'parol-12345'}")
            print("[cli] СЕЙЧАС смените пароль и включите 2FA в разделе Настройки")
        else:
            print(f"[cli] учётка не создана: {user.get('message')}")
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="AI-администратор: CLI (фаза 1)")
    parser.add_argument("--seed", action="store_true", help="залить демо-данные и выйти")
    parser.add_argument("--user", metavar="LOGIN", help="создать учётную запись панели и выйти")
    parser.add_argument("--role", default="owner", choices=["owner", "staff", "team"])
    parser.add_argument("--password", default="", help="пароль (минимум 8 символов)")
    parser.add_argument("--name", default=None, help="отображаемое имя")
    parser.add_argument("--salon", default=None, help="салон для owner/staff")
    parser.add_argument("--totp", action="store_true", help="включить 2FA и напечатать секрет")
    parser.add_argument(
        "--forget", metavar="PHONE", help="удалить данные клиента по запросу (152-ФЗ)"
    )
    parser.add_argument(
        "--onboard", action="store_true", help="создать точку целиком: салон, мастер, услуги"
    )
    parser.add_argument("--plan", default="trial", help="тариф новой точки (--onboard)")
    parser.add_argument("--master", default="Мастер", help="имя мастера (--onboard)")
    parser.add_argument("--login", default="boss@test.ru", help="логин владельца (--onboard)")
    parser.add_argument(
        "--timezone", default="Asia/Yekaterinburg", help="часовой пояс салона (--onboard)"
    )
    args = parser.parse_args()
    if args.seed:
        asyncio.run(do_seed())
    elif args.user:
        asyncio.run(do_user(args))
    elif args.forget:
        asyncio.run(do_forget(args))
    elif args.onboard:
        asyncio.run(do_onboard(args))
    else:
        asyncio.run(dialog())


if __name__ == "__main__":
    main()
