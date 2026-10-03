"""whatsapp-service — приём сообщений клиентов из WhatsApp (Meta Cloud API).

Зачем он: часть салонов живёт в WhatsApp, и там сейчас просто тишина — клиент
пишет, а ответа нет. Сервис принимает webhook Meta, проверяет подпись, находит
салон по номеру телефона и передаёт текст в тот же агент, что и Telegram.

Без WHATSAPP_TOKEN / WHATSAPP_PHONE_NUMBER_ID / WHATSAPP_VERIFY_TOKEN сервис
не стартует: молча принимать и терять сообщения хуже, чем не принимать вовсе.

Эндпоинты: ``GET /webhook`` — проверка токена Meta, ``POST /webhook`` —
входящее сообщение (подпись X-Hub-Signature-256), ``GET /health``.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from aiohttp import web

sys.path.insert(0, "/app")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "core"))

from admin_core.agent.agent import AgentSession  # noqa: E402
from admin_core.channels.whatsapp_auth import verify_signature  # noqa: E402
from admin_core.crm import make_crm  # noqa: E402
from admin_core.db import get_pool, run_migrations  # noqa: E402
from admin_core.health import (  # noqa: E402
    ServiceMetrics,
    health_payload,
    ping_db,
)
from admin_core.llm import make_provider  # noqa: E402

TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")
GRAPH_VERSION = os.getenv("WHATSAPP_GRAPH_VERSION", "v21.0")
API_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"
PORT = int(os.getenv("WHATSAPP_HTTP_PORT", "8080"))


class MissingConfig(RuntimeError):
    """Не задана обязательная переменная окружения."""


def required() -> None:
    missing = [
        name for name, value in (
            ("WHATSAPP_TOKEN", TOKEN),
            ("WHATSAPP_PHONE_NUMBER_ID", PHONE_ID),
            ("WHATSAPP_VERIFY_TOKEN", VERIFY_TOKEN),
        ) if not value
    ]
    if missing:
        raise MissingConfig(
            "WhatsApp не настроен, не хватает переменных: " + ", ".join(missing)
        )


def clean_phone(phone: str) -> str:
    """Только цифры: Meta пишет номера по-разному, клиент — тоже."""
    return "".join(ch for ch in phone if ch.isdigit())


async def find_salon(pool: Any, phone: str) -> dict[str, Any] | None:
    """Салон по номеру телефона клиента.

    Смотрим последний диалог, затем последнюю запись: иначе номер из другого
    салона отвечал бы в чужую точку.
    """
    digits = clean_phone(phone)
    row = await pool.fetchrow(
        "SELECT s.* FROM dialogs d JOIN clients c ON c.id = d.client_id "
        "JOIN salons s ON s.id = d.salon_id "
        "WHERE replace(replace(replace(c.phone, '+', ''), ' ', ''), '-', '') = $1 "
        "ORDER BY d.started_at DESC LIMIT 1",
        digits,
    )
    if row:
        return dict(row)
    row = await pool.fetchrow(
        "SELECT s.* FROM bookings b JOIN clients c ON c.id = b.client_id "
        "JOIN salons s ON s.id = b.salon_id "
        "WHERE replace(replace(replace(c.phone, '+', ''), ' ', ''), '-', '') = $1 "
        "ORDER BY b.starts_at DESC LIMIT 1",
        digits,
    )

async def _reply(
    pool: Any, llm: Any, crm: Any, salon: dict, phone: str, text: str,
    metrics: ServiceMetrics,
) -> None:
    """Ответить клиенту: агент ведёт диалог, CRM ставит запись."""
    import aiohttp  # noqa: PLC0415 — только на пути ответа

    client = await crm.ensure_client(str(salon["id"]), phone)
    dialog_id = await pool.fetchval(
        "INSERT INTO dialogs (salon_id, client_id, channel, chat_id) "
        "VALUES ($1, $2, 'whatsapp', $3) RETURNING id",
        str(salon["id"]), str(client["id"]), phone,
    )
    await pool.execute(
        "INSERT INTO dialog_messages (dialog_id, role, content) "
        "VALUES ($1, 'user', $2)",
        dialog_id, text,
    )
    session = AgentSession(crm, llm, salon, dict(client), created_by="bot:whatsapp")
    answer = (await session.run_turn(text)).strip() or "Записал запрос, скоро ответим."
    await pool.execute(
        "INSERT INTO dialog_messages (dialog_id, role, content) "
        "VALUES ($1, 'assistant', $2)",
        dialog_id, answer,
    )
    async with aiohttp.ClientSession() as http:
        await http.post(
            f"{API_BASE}/{PHONE_ID}/messages",
            json={
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "text",
                "text": {"body": answer},
            },
            headers={"Authorization": f"Bearer {TOKEN}"},
            timeout=20,
        )
    metrics.messages.labels("replies").inc()

async def build_app() -> web.Application:
    metrics = ServiceMetrics("whatsapp")
    pool = await get_pool()
    await run_migrations(pool)
    llm = make_provider()
    crm = make_crm(pool)
    app = web.Application(client_max_size=256 * 1024)
    app["pool"] = pool

    async def verify(request: web.Request) -> web.Response:
        if request.query.get("hub.mode") != "subscribe":
            return web.Response(status=400, text="bad mode")
        if request.query.get("hub.verify_token") != VERIFY_TOKEN:
            return web.Response(status=403, text="bad token")
        challenge = request.query.get("hub.challenge", "")
        return web.Response(text=challenge, content_type="text/plain")

    async def incoming(request: web.Request) -> web.Response:
        body = await request.read()
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not verify_signature(body, signature, APP_SECRET):
            # Без APP_SECRET пропустить проверку нельзя: подпись — единственное,
            # что отличает Meta от любого чужого POST в наш эндпоинт.
            metrics.errors.labels("signature").inc()
            return web.Response(status=401, text="bad signature")
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001 — не-JSON приходит только при ошибках Meta
            return web.Response(status=400, text="bad json")

        handled = 0
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages") or [{}]
                message = messages[0]
                text = (message.get("text") or {}).get("body", "")
                sender = message.get("from", "")
                if not text or not sender:
                    continue
                salon = await find_salon(pool, sender)
                if salon is None:
                    metrics.messages.labels("dropped").inc()
                    continue
                handled += 1
                await _reply(pool, llm, crm, salon, sender, text, metrics)
        return web.json_response({"handled": handled})

    async def health(_request: web.Request) -> web.Response:
        db_ok = await ping_db(pool)
        return web.json_response(
            health_payload(
                db_ok=db_ok, service=metrics.service, extra={"channel": "whatsapp"}
            ),
            status=200 if db_ok else 503,
        )

    async def metrics_endpoint(_request: web.Request) -> web.Response:
        return web.Response(
            body=metrics.render(),
            headers={"Content-Type": "text/plain; version=0.0.4; charset=utf-8"},
        )

    app.router.add_get("/webhook", verify)
    app.router.add_post("/webhook", incoming)
    app.router.add_get("/", lambda _r: web.json_response({"service": "whatsapp"}))
    app.router.add_get("/health", health)
    app.router.add_get("/metrics", metrics_endpoint)
    return app


async def main() -> None:
    """Сервис живёт в своём event loop: пул БД и приложение создаются"""
    try:
        required()
    except MissingConfig as exc:
        print(f"[whatsapp] {exc}", flush=True)
        sys.exit(1)
    app = await build_app()
    # AppRunner, а не web.run_app: run_app поднимает отдельный цикл, а пул
    # asyncpg был бы привязан к уже закрытому — и каждый запрос падал бы.
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)  # noqa: S104
    await site.start()
    print(f"[whatsapp] слушаю :{PORT}", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
