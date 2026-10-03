"""bot-service — Telegram-бот (long polling), фаза 2.

Роутер к общему «мозгу» admin_core: AgentSession + InternalCRM.
Клиент определяется по номеру телефона (кнопка «Поделиться номером»,
решение вопроса 7); до этого — временный клиент tg-{chat_id}.
Все сообщения пишутся в dialogs / dialog_messages (ПДн — только у нас,
в LLM уходит обезличенный текст через admin_core.anonymize).
pywa (WhatsApp) подключается следующим шагом на этот же роутер.
"""

import asyncio
import os
import sys
from zoneinfo import ZoneInfo

from admin_core.agent.agent import AgentSession
from admin_core.crm import make_crm
from admin_core.db import get_pool, run_migrations
from admin_core.health import ServiceMetrics, make_health_app, start_health_server
from admin_core.llm import make_provider
from admin_core.ratelimit import RateLimiter
from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Статусы записи словами для клиента: внутренние коды в чат не показываем.
STATUS_RU = {
    "booked": "ожидает подтверждения",
    "confirmed": "подтверждена",
    "completed": "выполнена",
    "cancelled": "отменена",
    "no_show": "не пришли",
}


def normalize_phone(raw: str) -> str:
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    return "+" + digits


def share_phone_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поделиться номером", request_contact=True)]],
        resize_keyboard=True,
    )


class BotState:
    """Пер-чат состояние в памяти; фаза 4 перенесёт в БД (мультиарендность)."""

    def __init__(self, pool, salon):
        self.pool = pool
        self.salon = dict(salon)
        self.crm = make_crm(pool, "internal")
        self.provider = make_provider()
        self.sessions: dict[int, AgentSession] = {}
        self.clients: dict[int, dict] = {}
        self.dialogs: dict[int, int] = {}

    async def session_for(
        self, chat_id: int, contact_phone: str | None = None
    ) -> AgentSession:
        if contact_phone:
            client = await self.crm.ensure_client(str(self.salon["id"]), contact_phone)
            self.clients[chat_id] = dict(client)
            self.sessions.pop(chat_id, None)  # пересобрать сущность под реального клиента
        if chat_id not in self.sessions:
            client = self.clients.get(chat_id) or dict(
                await self.crm.ensure_client(str(self.salon["id"]), f"tg-{chat_id}")
            )
            self.clients[chat_id] = client
            self.sessions[chat_id] = AgentSession(
                self.crm, self.provider, self.salon, client, created_by="bot:telegram"
            )
            self.dialogs[chat_id] = await self.pool.fetchval(
                "INSERT INTO dialogs (salon_id, client_id, channel) VALUES ($1, $2, 'telegram') "
                "RETURNING id",
                str(self.salon["id"]), str(client["id"]),
            )
        return self.sessions[chat_id]

    async def log(self, chat_id: int, role: str, content: str) -> None:
        dialog_id = self.dialogs.get(chat_id)
        if dialog_id:
            await self.pool.execute(
                "INSERT INTO dialog_messages (dialog_id, role, content) VALUES ($1, $2, $3)",
                dialog_id, role, content,
            )


async def main() -> None:
    if not TOKEN:
        print("[bot] TELEGRAM_BOT_TOKEN не задан — добавьте его в docker/.env")
        sys.exit(1)

    pool = await get_pool()
    await run_migrations(pool)
    async with pool.acquire() as conn:
        salon = await conn.fetchrow(
            "SELECT * FROM salons WHERE is_active ORDER BY created_at LIMIT 1"
        )
    if not salon:
        print("[bot] нет салона в БД — сначала: docker compose run --rm cli python cli.py --seed")
        sys.exit(1)

    state = BotState(pool, salon)
    metrics = ServiceMetrics("bot-service")
    limiter = RateLimiter(
        limit=int(os.getenv("TG_RATE_LIMIT", "6")),
        window=float(os.getenv("TG_RATE_WINDOW", "10")),
    )
    # TG_API_BASE — обход блокировки api.telegram.org через Cloudflare Worker.
    # from_base() подставляет шаблоны: {base}/bot{token}/{method} и /file/bot{token}/{path}
    tg_base = os.getenv("TG_API_BASE")
    session = (
        AiohttpSession(api=TelegramAPIServer.from_base(tg_base))
        if tg_base else AiohttpSession()
    )
    bot = Bot(TOKEN, session=session)
    print(f"[bot] Telegram API: {tg_base or 'https://api.telegram.org'}", flush=True)
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def start(message: Message) -> None:
        await state.session_for(message.chat.id)
        await message.answer(
            f"Здравствуйте! Салон «{state.salon['name']}», администратор Анна.\n"
            "Нажмите «📱 Поделиться номером» — найду вашу карточку, "
            "или просто напишите вопрос.",
            reply_markup=share_phone_keyboard(),
        )

    @dp.message(F.contact)
    async def contact(message: Message) -> None:
        phone = normalize_phone(message.contact.phone)
        await state.session_for(message.chat.id, contact_phone=phone)
        await message.answer(f"Спасибо! Вижу ваш номер ({phone[:4]}***). Чем помочь?")

    @dp.message(F.text)
    async def text(message: Message) -> None:
        verdict = limiter.check(str(message.chat.id), message.text or "")
        if not verdict.allowed:
            metrics.rate_limited.labels(verdict.reason).inc()
            if verdict.warn:
                await message.answer(
                    "Кажется, сообщения идут слишком часто — напишите, "
                    "пожалуйста, одним сообщением, я обязательно отвечу."
                )
            return
        metrics.messages.labels("user").inc()
        session = await state.session_for(message.chat.id)
        await state.log(message.chat.id, "user", message.text)
        try:
            reply = await session.run_turn(message.text)
        except Exception:  # noqa: BLE001 — клиенту не показываем трейсы
            metrics.errors.labels("run_turn").inc()
            reply = "Извините, техническая неполадка — передаю старшему администратору."
        metrics.messages.labels("assistant").inc()
        await state.log(message.chat.id, "assistant", reply)
        await message.answer(reply)

    @dp.callback_query(F.data.startswith("remind:"))
    async def reminder_button(callback) -> None:
        """Клиент нажал «Подтверждаю / Перенести / Отменить» в напоминании.

        Ответ пишем прямо в запись: подтверждение снимает вопрос «дойдёт ли»,
        отмена сразу освобождает слот и запускает лист ожидания.
        """
        from admin_core.crm.internal import InternalCRM  # noqa: PLC0415

        action = (callback.data or "").split(":", 1)[-1]
        answer = {
            "ok": "Записали: вы придёте. Ждём вас!",
            "move": "Понял. Позвоните в салон, и мы подберём другое время.",
            "cancel": "Жаль. Запись отменена — слот освободили для других.",
        }.get(action, "Не понял нажатие — позвоните в салон, и мы поправим.")
        booking = None
        chat_id = callback.message.chat.id if callback.message else 0
        client_id = await state.pool.fetchval(
            "SELECT client_id FROM dialogs WHERE chat_id = $1 ORDER BY id DESC LIMIT 1",
            chat_id,
        )
        if client_id:
            booking = await state.pool.fetchrow(
                "SELECT b.id, b.salon_id FROM bookings b "
                "WHERE b.client_id = $1 AND b.status IN ('booked','confirmed') "
                "ORDER BY b.starts_at LIMIT 1",
                client_id,
            )
        if booking and action in ("ok", "cancel"):
            crm = InternalCRM(state.pool)
            new_status = "confirmed" if action == "ok" else "cancelled"
            await crm.set_booking_status(
                str(booking["salon_id"]), str(booking["id"]), new_status,
                actor="bot:reminder",
            )
            await state.log(chat_id, "user", f"нажата кнопка: {action}")
        await callback.answer(answer[:200], show_alert=False)
        if callback.message:
            await callback.message.answer(answer)

    @dp.message(Command("bookings"))
    async def my_bookings(message: Message) -> None:
        """Клиент сам смотрит и отменяет записи — без оператора.

        Раньше любой вопрос про запись шёл человеку: это и нагрузка, и ожидание.
        """
        client_id = await state.pool.fetchval(
            "SELECT client_id FROM dialogs WHERE chat_id = $1 ORDER BY id DESC LIMIT 1",
            message.chat.id,
        )
        if not client_id:
            await message.answer("Я вас ещё не узнал. Нажмите «📱 Поделиться номером».")
            return
        rows = await state.pool.fetch(
            "SELECT b.starts_at, b.status, sv.title "
            "FROM bookings b JOIN services sv ON sv.id = b.service_id "
            "WHERE b.client_id = $1 AND b.starts_at >= now() - interval '2 hours' "
            "ORDER BY b.starts_at LIMIT 5",
            client_id,
        )
        if not rows:
            await message.answer(
                "Предстоящих записей нет. Записаться можно прямо здесь — скажите, "
                "на какое время удобно."
            )
            return
        tz = ZoneInfo(state.salon.get("timezone") or "Asia/Yekaterinburg")
        lines = ["Ваши записи:"]
        buttons = []
        for row in rows:
            when = row["starts_at"].astimezone(tz).strftime("%d.%m в %H:%M")
            lines.append(f"• {when} — {row['title']} ({STATUS_RU.get(row['status'], row['status'])})")
            if row["status"] in ("booked", "confirmed"):
                buttons.append([
                    InlineKeyboardButton(
                        text=f"Отменить {when}",
                        callback_data=f"cancel:{row['starts_at'].timestamp():.0f}",
                    )
                ])
        markup = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
        await message.answer("\n".join(lines), reply_markup=markup)

    @dp.callback_query(F.data.startswith("cancel:"))
    async def cancel_booking(callback) -> None:
        """Клиент отменил запись сам — слот освобождается, слот уходит в лист ожидания."""
        from admin_core.crm.internal import InternalCRM  # noqa: PLC0415

        client_id = await state.pool.fetchval(
            "SELECT client_id FROM dialogs WHERE chat_id = $1 ORDER BY id DESC LIMIT 1",
            callback.message.chat.id if callback.message else 0,
        )
        if not client_id:
            await callback.answer("Сначала поделитесь номером", show_alert=True)
            return
        booking = await state.pool.fetchrow(
            "UPDATE bookings SET status = 'cancelled' WHERE client_id = $1 "
            "AND status IN ('booked','confirmed') AND starts_at >= now() "
            "ORDER BY starts_at LIMIT 1 RETURNING id, salon_id",
            client_id,
        )
        if not booking:
            await callback.answer("Запись уже отменена или её нет", show_alert=True)
            return
        await InternalCRM(state.pool).set_booking_status(
            str(booking["salon_id"]), str(booking["id"]), "cancelled", actor="bot:telegram"
        )
        await callback.answer("Отменено. Если захотите вернуться — просто напишите",
                              show_alert=False)

    @dp.message(Command("help"))
    async def help_cmd(message: Message) -> None:
        """Короткая справка: что бот умеет и как записаться."""
        await message.answer(
            "Я администратор салона «Анна». Умею:\n"
            "• показать свободное время и записать вас (/bookings — ваши записи);\n"
            "• напомнить о визите за сутки и за два часа;\n"
            "• отменить или перенести запись — кнопками прямо в сообщении.\n\n"
            "Напишите, на что хотите записаться, и на какое время удобно.",
        )

    me = await bot.get_me()
    print(f"[bot] polling as @{me.username} (LLM: {state.provider.name})", flush=True)
    http_port = int(os.getenv("BOT_HTTP_PORT", "8080"))
    health_runner = await start_health_server(
        make_health_app(
            metrics,
            pool=pool,
            provider=state.provider.name,
            extra=lambda: {"chat_sessions": len(state.sessions)},
        ),
        port=http_port,
    )
    print(f"[bot] health: :{http_port}/health | metrics: :{http_port}/metrics", flush=True)
    try:
        await dp.start_polling(bot)
    finally:
        await health_runner.cleanup()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())

