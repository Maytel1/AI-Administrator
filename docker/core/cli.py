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


def main() -> None:
    parser = argparse.ArgumentParser(description="AI-администратор: CLI (фаза 1)")
    parser.add_argument("--seed", action="store_true", help="залить демо-данные и выйти")
    args = parser.parse_args()
    if args.seed:
        asyncio.run(do_seed())
    else:
        asyncio.run(dialog())


if __name__ == "__main__":
    main()
