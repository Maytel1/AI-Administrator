"""worker — фоновые задачи: напоминания, лист ожидания, тревоги.

Раньше был скелетом фазы 0. Теперь делает то, что должно происходить без
человека: пишет клиентам напоминания и поднимает тревогу, когда что-то сломалось.

Три правила, без которых такой сервис становится источником жалоб:
1. **Ничего не отправляется дважды** — уникальный ключ в БД, а не флаг в памяти;
2. **Ошибка отправки не роняет цикл** — пишем статус failed и идём дальше;
3. **Тревоги не повторяются часами** — иначе телефон владельца перестанет быть
   полезным инструментом.
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import asyncpg
from admin_core.health import ServiceMetrics, make_health_app, start_health_server
from admin_core.monitoring import check_alerts, render_alert, should_send
from admin_core.reminders import claim_reminder, due_reminders, mark_reminder_failed
from admin_core.waitlist import waitlist_size

CONFIG_KEYS = {
    "YC_API_KEY": "Yandex Cloud — YandexGPT/SpeechKit",
    "TELEGRAM_BOT_TOKEN": "уведомления владельцу и клиентам",
    "OWNER_TG_CHAT_ID": "куда слать тревоги (если не пусто)",
}

TICK_SECONDS = int(os.getenv("WORKER_TICK", "60"))
ALERT_CHAT = os.getenv("OWNER_TG_CHAT_ID", "").strip()


def cfg_report() -> str:
    return "\n".join(
        f"  {key}: {'set' if os.getenv(key) else '-'} ({hint})"
        for key, hint in CONFIG_KEYS.items()
    )


class TelegramSender:
    """Минимальный отправитель: один POST на сообщение, без лишних зависимостей.

    Отдельный класс, а не aiogram, потому что worker'у не нужен апдейтер: он
    только отправляет. Токен берём из окружения на каждый вызов — тогда смена
    токена не требует перезапуска процесса.
    """

    def __init__(self, base: str = "") -> None:
        self.base = base or "https://api.telegram.org"

    async def send(self, chat_id: str, text: str, *, reply_markup: dict | None = None) -> bool:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not token or not chat_id:
            return False
        payload: dict[str, object] = {"chat_id": chat_id, "text": text}
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        try:
            import aiohttp  # noqa: PLC0415 — только когда реально отправляем

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base}/bot{token}/sendMessage?{urlencode(payload)}",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    return response.status == 200
        except Exception as exc:  # noqa: BLE001 — сеть отвалится, это не повод умереть
            print(f"[worker] отправка не удалась: {type(exc).__name__}", flush=True)
            return False

REMINDER_KEYBOARD = {
    "inline_keyboard": [[
        {"text": "✅ Подтверждаю", "callback_data": "remind:ok"},
        {"text": "📅 Перенести", "callback_data": "remind:move"},
        {"text": "❌ Отменить", "callback_data": "remind:cancel"},
    ]]
}

async def find_chat_id(pool: asyncpg.Pool, phone: str) -> str | None:
    """chat_id клиента по телефону — напоминание идёт в тот же чат, где он писал."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(digits) < 10:
        return None
    row = await pool.fetchrow(
        "SELECT chat_id::text FROM dialogs "
        "WHERE client_id = (SELECT id FROM clients WHERE phone LIKE '%' || $1 LIMIT 1) "
        "ORDER BY id DESC LIMIT 1",
        digits[-10:],
    )
    return row["chat_id"] if row else None


async def process_salon(
    pool: asyncpg.Pool, salon: dict, sender: TelegramSender, metrics: ServiceMetrics
) -> dict:
    """Один проход по точке: напоминания клиентам + размер листа ожидания."""
    salon_id = str(salon["id"])
    stats = {"reminders": 0, "failed": 0, "skipped": 0}
    for item in await due_reminders(pool, salon_id):
        chat_id = await find_chat_id(pool, item.get("client_phone") or "")
        if not chat_id and item.get("client_phone") is None:
            phone = await pool.fetchval(
                "SELECT c.phone FROM bookings b JOIN clients c ON c.id = b.client_id "
                "WHERE b.id = $1",
                item["booking_id"],
            )
            chat_id = await find_chat_id(pool, phone or "")
        if not chat_id:
            # Клиент ни разу не писал боту — отправлять ему некуда. Помечаем
            # один раз, чтобы не перебирать его запись на каждом тике.
            if await claim_reminder(pool, item["booking_id"], item["kind"], status="skipped"):
                stats["skipped"] += 1
            continue
        if not await claim_reminder(pool, item["booking_id"], item["kind"]):
            continue  # гонка: другой инстанс уже отправил это же напоминание
        if await sender.send(chat_id, item["text"], reply_markup=REMINDER_KEYBOARD):
            stats["reminders"] += 1
            metrics.messages.labels("reminder").inc()
        else:
            await mark_reminder_failed(pool, item["booking_id"], item["kind"])
            stats["failed"] += 1
            metrics.errors.labels("reminder_failed").inc()
    stats["waitlist"] = await waitlist_size(pool, salon_id)
    return stats


async def process_auto_invoices(
    pool: asyncpg.Pool, metrics: ServiceMetrics
) -> dict[str, Any]:
    """Счета по дате из карточки плательщика. Логика — в admin_core.billing,"""
    from admin_core.billing import auto_invoice_tick  # noqa: PLC0415

    result = await auto_invoice_tick(pool)
    for payment_id in result["issued"]:
        metrics.messages.labels("auto_invoice").inc()
        print(f"[worker] счёт #{payment_id[:8]} выставлен", flush=True)
    for problem in result["errors"]:
        metrics.errors.labels("auto_invoice").inc()
        print(f"[worker] счёт не выставлен: {problem}", flush=True)
    return result


async def process_alerts(
    pool: asyncpg.Pool, sender: TelegramSender, metrics: ServiceMetrics
) -> int:
    """Отправить тревоги, для которых с прошлого раза прошёл час."""
    sent = 0
    for alert in await check_alerts(pool):
        if not await should_send(pool, alert.kind):
            continue
        if ALERT_CHAT and await sender.send(ALERT_CHAT, render_alert(alert)):
            sent += 1
            metrics.messages.labels("alert").inc()
    return sent


async def process_retention(pool: asyncpg.Pool, metrics: ServiceMetrics) -> dict[str, object]:
    """Раз в сутки чистим ПДн старше срока хранения.

    Запуск в полночь по локальному времени: нагрузка в этот момент нулевая,
    а если worker пропустил тик — следующий день ничего не исправит.
    """
    from admin_core.retention import purge_personal_data  # noqa: PLC0415

    now = datetime.now(ZoneInfo("UTC"))
    if now.hour != 3:
        return {"skipped": True}
    stats = await purge_personal_data(pool)
    removed = int(stats["dialog_messages"]) + int(stats["media_rows"])
    if removed:
        metrics.messages.labels("pdn_purged", str(stats["dialog_messages"])).inc()
        print(f"[worker] удалено по сроку хранения: {stats}", flush=True)
    return stats


async def main() -> None:
    name = os.getenv("SERVICE_NAME", "worker")
    print(f"[{name}] старт: напоминания, лист ожидания, тревоги", flush=True)
    print(f"[{name}] конфигурация:\n{cfg_report()}", flush=True)

    dsn = os.getenv("DATABASE_URL", "postgresql://aiadmin:aiadmin_dev@postgres:5432/aiadmin")
    pool: asyncpg.Pool | None = None
    for attempt in range(1, 31):
        try:
            pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
            version = (await pool.fetchval("select version()")).split(",")[0]
            print(f"[{name}] PostgreSQL OK: {version}", flush=True)
            break
        except Exception as exc:  # noqa: BLE001 — просто ждём подъём БД
            print(f"[{name}] ждём PostgreSQL ({attempt}/30): {type(exc).__name__}", flush=True)
            await asyncio.sleep(2)
    else:
        print(f"[{name}] PostgreSQL так и не поднялся — выход", flush=True)
        sys.exit(1)

    metrics = ServiceMetrics("worker")
    sender = TelegramSender(os.getenv("TG_API_BASE", ""))
    port = int(os.getenv("WORKER_HTTP_PORT", "8084"))
    health = await start_health_server(
        make_health_app(
            metrics, pool=pool, provider="worker",
            extra=lambda: {"tick_seconds": TICK_SECONDS},
        ),
        port=port,
    )
    print(f"[{name}] health: :{port}/health | тик: {TICK_SECONDS} c", flush=True)

    try:
        while True:
            started = datetime.now(ZoneInfo("UTC"))
            try:
                for salon in await pool.fetch(
                    "SELECT id, name, timezone FROM salons WHERE is_active"
                ):
                    await process_salon(pool, dict(salon), sender, metrics)
                await process_alerts(pool, sender, metrics)
                await process_retention(pool, metrics)
                await process_auto_invoices(pool, metrics)
            except Exception as exc:  # noqa: BLE001 — цикл не должен умирать
                metrics.errors.labels("tick").inc()
                print(f"[{name}] ошибка тика: {type(exc).__name__}: {exc}", flush=True)
            elapsed = (datetime.now(ZoneInfo("UTC")) - started).total_seconds()
            await asyncio.sleep(max(1.0, TICK_SECONDS - elapsed))
    finally:
        await health.cleanup()
        if pool is not None:
            await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
