"""call-service — голосовой контур (фаза 3, DEVELOPMENT_PLAN.md).

**Работает:** жизненный цикл звонка (start → answer → end), запись в
``dialogs``/``dialog_messages``, уведомление клиента об обработке звонка ИИ
(требование 152-ФЗ), автоответчик, пока не подключены STT/LLM (failover из
FAILOVER_SCENARIOS.md), health и метрики.

**Не подключено:** распознавание и синтез речи (SpeechKit) и SIP-телефония —
внешние сервисы с ключами и оплатой. Без них сервис честно отвечает
автоответчиком, а не делает вид, что говорит с клиентом.

Маршруты: POST /call/start, /call/answer, /call/end, GET /health, /metrics.
"""

import asyncio
import os
import sys
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
from admin_core.health import ServiceMetrics, make_health_app, start_health_server

CONFIG_KEYS = {
    "YC_API_KEY": "Yandex Cloud — SpeechKit STT/TTS",
    "YC_FOLDER_ID": "Yandex Cloud — каталог",
    "SIP_LOGIN": "SIP-аккаунт телефонии",
    "SIP_PASSWORD": "SIP-аккаунт телефонии",
    "CALL_API_TOKEN": "токен для вызовов /call/* (обязателен)",
}

# Уведомление о записи разговора — первое, что слышит клиент (ст.9 152-ФЗ:
# согласие на обработку ПДн должно быть конкретным, информированным и
# сознательным). «Продолжая, вы соглашаетесь» псевдосогласием не является:
# у клиента не было реальной возможности отказаться, не положив трубку. Поэтому
# текст называет факт записи, ИИ и — главное — даёт явный выход: нажмите «0»,
# и разговор не будет записан, звонок уйдёт оператору. Состояние согласия
# фиксируем в сессии (consent), чтобы запись велась только при согласии.
DISCLOSURE = (
    "Здравствуйте! Ваш звонок принимает искусственный интеллект. "
    "Разговор может быть записан для улучшения качества обслуживания. "
    "Если вы не хотите, чтобы звонок записывался, нажмите 0 — мы переключим "
    "звонок на оператора. Подробнее о защите данных — в политике обработки "
    "персональных данных на нашем сайте."
)
# Фраза отказа от записи: клиент явно просит не записывать / зовёт оператора.
DECLINE_WORDS = ("0", "ноль", "оператор", "человек", "не записывай", "без записи")
# Что говорим, когда клиент отказался: подтверждаем и переключаем на человека.
NO_RECORD_REPLY = (
    "Понял вас. Запись этого разговора выключена. Соединяю с оператором салона."
)
ANSWER_MACHINE = (
    "Здравствуйте! Салон временно не может ответить. Оставьте имя и телефон — "
    "мы перезвоним в рабочее время."
)
FAILOVER_REASON = "llm_unavailable"


def is_decline(text: str) -> bool:
    """Клиент отказался от записи (нажал «0» или попросил оператора).

    Простое вхождение подстроки, а не NLU: сервис пока без STT/LLM, и точнее
    тут не нужно — важна сама возможность отказа, а не её качество.
    """
    lowered = (text or "").strip().lower()
    return any(word in lowered for word in DECLINE_WORDS)


def cfg_report() -> str:
    return "\n".join(
        f"  {key}: {'set' if os.getenv(key) else '-'} ({hint})"
        for key, hint in CONFIG_KEYS.items()
    )


def voice_ready() -> bool:
    """Голос работает только с ключом и каталогом; иначе это failover."""
    return bool(os.getenv("YC_API_KEY")) and bool(os.getenv("YC_FOLDER_ID"))


def token_matches(supplied: str, expected: str) -> bool:
    """Сравнение токена константным временем.

    Правило живёт в ``admin_core.tokens``: пустой секрет не подходит ни к чему
    (сервис без CALL_API_TOKEN закрыт, а не открыт), а не-ASCII значение не
    роняет обработчик, как было бы при сравнении строк.
    """
    from admin_core.tokens import matches  # noqa: PLC0415

    return matches(supplied, expected)


class CallSession:
    """Один звонок: кто звонил, что сказал, чем закончилось."""

    def __init__(self, pool: Any, salon: dict[str, Any]) -> None:
        self.pool = pool
        self.salon = dict(salon)
        self.sessions: dict[str, dict[str, Any]] = {}

    async def start(self, call_id: str, phone: str) -> dict[str, Any]:
        from admin_core.crm.internal import InternalCRM  # noqa: PLC0415

        client = await InternalCRM(self.pool).ensure_client(
            str(self.salon["id"]), phone, None
        )
        dialog_id = await self.pool.fetchval(
            "INSERT INTO dialogs (salon_id, client_id, channel, chat_id) "
            "VALUES ($1, $2, 'voice', $3) RETURNING id",
            str(self.salon["id"]), client["id"], call_id,
        )
        await self.pool.execute(
            "INSERT INTO dialog_messages (dialog_id, role, content) VALUES ($1,$2,$3)",
            dialog_id, "assistant", DISCLOSURE,
        )
        self.sessions[call_id] = {
            "dialog_id": dialog_id, "client_id": str(client["id"]),
            "started_at": datetime.now(ZoneInfo("UTC")),
            # Согласие на запись: по умолчанию предполагаем, но гасим сразу,
            # как только клиент отказался (см. answer). Нужен для честного 152-ФЗ.
            "consent": True, "recording": True,
        }
        return {
            "call_id": call_id,
            "mode": "voice" if voice_ready() else "answer_machine",
            "reason": None if voice_ready() else FAILOVER_REASON,
            "text": DISCLOSURE if voice_ready() else ANSWER_MACHINE,
        }

    async def answer(self, call_id: str, text: str) -> dict[str, Any]:
        session = self.sessions.get(call_id)
        if not session:
            return {"ok": False, "error": "unknown_call"}
        # Клиент отказался от записи (152-ФЗ): гасим согласие и больше не
        # сохраняем его реплики — иначе «возможность отказа» была бы фикцией,
        # а текст разговора всё равно осел бы в БД как персональные данные.
        if session.get("recording") and is_decline(text):
            session["consent"] = False
            session["recording"] = False
        if session.get("recording"):
            await self.pool.execute(
                "INSERT INTO dialog_messages (dialog_id, role, content) VALUES ($1, $2, $3)",
                session["dialog_id"], "user", (text or "")[:4000],
            )
        # Отказ — переключаем на оператора и прекращаем запись этого звонка.
        if not session.get("consent"):
            return {
                "ok": True, "text": NO_RECORD_REPLY, "mode": "operator",
                "recording": False, "consent": False,
            }
        if voice_ready():
            return {"ok": True, "text": "", "note": "диалог ведёт LLM"}
        return {"ok": True, "text": ANSWER_MACHINE, "mode": "answer_machine"}

    async def end(self, call_id: str, *, outcome: str = "completed") -> dict[str, Any]:
        session = self.sessions.pop(call_id, None)
        if not session:
            return {"ok": False, "error": "unknown_call"}
        # Если клиент отказался от записи — убираем всё, что успели сохранить
        # до отказа: реплики до момента отказа — тоже персональные данные, и
        # «возможность отказа» обязана стирать их, а не оставлять в БД.
        if not session.get("consent", True):
            await self.pool.execute(
                "DELETE FROM dialog_messages WHERE dialog_id = $1",
                session["dialog_id"],
            )
            outcome = outcome if outcome != "completed" else "no_consent"
        await self.pool.execute(
            "UPDATE dialogs SET ended_at = now(), outcome = $2 WHERE id = $1",
            session["dialog_id"], outcome,
        )
        seconds = round(
            (datetime.now(ZoneInfo("UTC")) - session["started_at"]).total_seconds()
        )
        return {"ok": True, "duration_sec": seconds}


async def _json_body(request: Any) -> dict[str, Any] | None:
    """Разбор тела запроса. Битый JSON — это 400, а не 500.

    Иначе любой мусор в теле ответа даёт ошибку сервера: это и лишний шум в
    логах, и повод для ложной тревоги «сервис падает».
    """
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001 — клиент прислал не JSON
        return None
    return data if isinstance(data, dict) else None


def build_app(pool: Any, metrics: ServiceMetrics, calls: CallSession) -> Any:
    """HTTP-контур звонков. Без токена сервис не принимает ни одного запроса."""
    from aiohttp import web  # noqa: PLC0415 — в cli-контейнере aiohttp нет

    token = os.getenv("CALL_API_TOKEN", "")

    @web.middleware
    async def guard(request: Any, handler: Any) -> Any:
        supplied = request.headers.get("Authorization", "")
        if supplied.startswith("Bearer "):
            supplied = supplied[7:]
        if not token_matches(supplied, token):
            resp: Any = web.Response(status=401, text="unauthorized")
        else:
            resp = await handler(request)
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    async def start_call(request: Any) -> Any:
        data = await _json_body(request)
        if data is None:
            return web.json_response({"ok": False, "error": "bad_json"}, status=400)
        phone = str(data.get("phone", "")).strip()
        if len("".join(ch for ch in phone if ch.isdigit())) < 10:
            return web.json_response({"ok": False, "error": "bad_phone"}, status=400)
        call_id = str(data.get("call_id", "")).strip() or phone
        metrics.messages.labels("call_start").inc()
        return web.json_response(await calls.start(call_id, phone))

    async def answer_call(request: Any) -> Any:
        data = await _json_body(request)
        if data is None:
            return web.json_response({"ok": False, "error": "bad_json"}, status=400)
        result = await calls.answer(str(data.get("call_id", "")), str(data.get("text", "")))
        if not result["ok"]:
            return web.json_response(result, status=404)
        metrics.messages.labels("call_turn").inc()
        return web.json_response(result)

    async def end_call(request: Any) -> Any:
        data = await _json_body(request)
        if data is None:
            return web.json_response({"ok": False, "error": "bad_json"}, status=400)
        result = await calls.end(
            str(data.get("call_id", "")), outcome=str(data.get("outcome", "completed"))
        )
        if not result["ok"]:
            return web.json_response(result, status=404)
        metrics.messages.labels("call_end").inc()
        return web.json_response(result)

    app = web.Application(middlewares=[guard])
    app.router.add_post("/call/start", start_call)
    app.router.add_post("/call/answer", answer_call)
    app.router.add_post("/call/end", end_call)
    return app


async def main() -> None:
    name = os.getenv("SERVICE_NAME", "call-service")
    print(f"[{name}] голосовой контур (фаза 3)", flush=True)
    print(f"[{name}] конфигурация:\n{cfg_report()}", flush=True)
    if not voice_ready():
        print(f"[{name}] ВНИМАНИЕ: нет YC_API_KEY/YC_FOLDER_ID — автоответчик", flush=True)
    if not os.getenv("CALL_API_TOKEN"):
        print(f"[{name}] ВНИМАНИЕ: нет CALL_API_TOKEN — запросы не принимаются", flush=True)

    dsn = os.getenv("DATABASE_URL", "postgresql://aiadmin:aiadmin_dev@postgres:5432/aiadmin")
    pool: Any = None
    for attempt in range(1, 31):
        try:
            pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
            print(f"[{name}] PostgreSQL OK", flush=True)
            break
        except Exception as exc:  # noqa: BLE001 — просто ждём БД
            print(f"[{name}] ждём PostgreSQL ({attempt}/30): {type(exc).__name__}", flush=True)
            await asyncio.sleep(2)
    else:
        print(f"[{name}] PostgreSQL так и не поднялся — выход", flush=True)
        sys.exit(1)

    async with pool.acquire() as conn:
        salon = await conn.fetchrow(
            "SELECT * FROM salons WHERE is_active ORDER BY created_at LIMIT 1"
        )
    if not salon:
        print("[call] нет активного салона — сначала cli.py --seed", flush=True)
        sys.exit(1)

    from aiohttp import web  # noqa: PLC0415

    metrics = ServiceMetrics("call-service")
    calls = CallSession(pool, dict(salon))
    # Порт внутри контейнера задаётся отдельно от проброса на хост: иначе
    # CALL_HTTP_PORT=8081 (это порт НА хосте) попадёт внутрь и сервис
    # будет слушать там, куда docker пробрасывает 8080.
    port = int(os.getenv("CALL_API_PORT", "8080"))
    health_port = port + 1
    runner = web.AppRunner(build_app(pool, metrics, calls))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    health = await start_health_server(
        make_health_app(
            metrics, pool=pool,
            provider="voice" if voice_ready() else "answer_machine",
        ),
        port=health_port,
    )
    print(f"[{name}] API :{port}/call/start | health :{health_port}/health", flush=True)
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await health.cleanup()
        await runner.cleanup()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
