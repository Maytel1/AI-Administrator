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


def main() -> None:
    parser = argparse.ArgumentParser(description="AI-администратор: CLI (фаза 1)")
    parser.add_argument("--seed", action="store_true", help="залить демо-данные и выйти")
    parser.add_argument("--user", metavar="LOGIN", help="создать учётную запись панели и выйти")
    parser.add_argument("--role", default="owner", choices=["owner", "staff", "team"])
    parser.add_argument("--password", default="", help="пароль (минимум 8 символов)")
    parser.add_argument("--name", default=None, help="отображаемое имя")
    parser.add_argument("--salon", default=None, help="салон для owner/staff")
    parser.add_argument("--totp", action="store_true", help="включить 2FA и напечатать секрет")
    args = parser.parse_args()
    if args.seed:
        asyncio.run(do_seed())
    elif args.user:
        asyncio.run(do_user(args))
    else:
        asyncio.run(dialog())


if __name__ == "__main__":
    main()
