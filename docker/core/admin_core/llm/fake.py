"""Детерминированный провайдер без API-ключей: демо и pytest.

Эмулирует полный сценарий записи (услуги → слоты → бронь → подтверждение),
обрабатывает занятый слот и честный ответ на вопрос «вы робот?»
(решения 20.09.2026, вопросы 8/11/12 и риск в DEVELOPMENT_PLAN.md).
"""

import json
import re

from .base import LLMProvider, LLMResponse, ToolCall

CONFIRM_TEXT = (
    "Готово! Записал(а) вас на {service} — {when}, мастер {master}. "
    "Ждём вас, до встречи!"
)
ROBOT_ANSWER = (
    "Меня зовут Анна, я администратор салона. Если вопрос «вы робот?» — "
    "отвечу честно: я цифровой ассистент, но веду запись не хуже живого "
    "администратора. Чем могу помочь?"
)
FAREWELL = "Спасибо, что выбрали нас! Ждём вас. Хорошего дня!"
IDLE = "Подскажите, что вас интересует: записаться, узнать цены или перенести запись?"


class FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self) -> None:
        self.pending_service: str | None = None
        self.offered_slots: list[dict] = []

    # ── основной вход ────────────────────────────────────────────────────
    async def chat(self, messages: list[dict], tools=None) -> LLMResponse:
        last_tool_idx = max(
            (i for i, m in enumerate(messages) if m["role"] == "tool"), default=-1
        )
        last_user_idx = max(
            (i for i, m in enumerate(messages) if m["role"] == "user"), default=-1
        )
        # результат инструмента обрабатываем, только если он НОВЕЕ реплики клиента
        if last_tool_idx > last_user_idx:
            data = json.loads(messages[last_tool_idx]["content"])
            return self._after_tool(messages[last_tool_idx].get("name", ""), data)

        last_user = messages[last_user_idx]["content"].lower() if messages else ""

        if "робот" in last_user or "бот " in last_user or "искусствен" in last_user:
            return LLMResponse(text=ROBOT_ANSWER)
        if "отмен" in last_user:
            return LLMResponse(tool_calls=[ToolCall("cancel_my_last_booking", {})])
        if "цен" in last_user or "услуг" in last_user or "сколько стоит" in last_user:
            return LLMResponse(tool_calls=[ToolCall("list_services", {})])
        if any(k in last_user for k in ("запис", "хочу", "стрижк", "маникюр", "окрас")):
            svc = self._find_service(last_user)
            if svc:
                self.pending_service = svc
                return LLMResponse(
                    tool_calls=[ToolCall("get_free_slots", {"service": svc})]
                )
            return LLMResponse(tool_calls=[ToolCall("list_services", {})])
        m = re.search(r"([01]?\d|2[0-3]):(\d{2})", last_user)
        if m and self.pending_service and self.offered_slots:
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        "create_booking",
                        {
                            "service": self.pending_service,
                            "time": f"{int(m.group(1)):02d}:{m.group(2)}",
                        },
                    )
                ]
            )
        if "спасибо" in last_user or "пока" in last_user:
            return LLMResponse(text=FAREWELL)
        return LLMResponse(text=IDLE)

    # ── реакция на результаты инструментов ───────────────────────────────
    def _after_tool(self, name: str, data: dict) -> LLMResponse:
        if name == "list_services":
            lines = "\n".join(
                f"• {s['title']} — {s['duration_min']} мин, {s['price']} ₽"
                for s in data.get("services", [])
            )
            return LLMResponse(text=f"У нас есть:\n{lines}\nЧто выбираете?")
        if name == "get_free_slots":
            slots = data.get("slots", [])
            self.offered_slots = slots
            if not slots:
                return LLMResponse(text="Свободного времени не нашлось, извините. Попробуем другой день?")
            shown = ", ".join(s["start_text"] for s in slots[:5])
            return LLMResponse(
                text=f"Свободно: {shown} (мастер {slots[0]['master_name']}). Какое время вам удобно?"
            )
        if name == "create_booking":
            if data.get("ok"):
                extra = (
                    " По правилам салона нужна небольшая предоплата — ссылку пришлём в мессенджер."
                    if data.get("prepayment_required")
                    else ""
                )
                return LLMResponse(
                    text=CONFIRM_TEXT.format(
                        service=data["service_title"],
                        when=data["when"],
                        master=data["master_name"],
                    )
                    + extra
                )
            if data.get("error") == "slot_taken":
                rest = [
                    s["start_text"]
                    for s in self.offered_slots
                    if s["start_text"] not in json.dumps(data.get("attempted", ""))
                ]
                offer = ", ".join(rest[:5]) or "другие дни — уточню у администратора"
                return LLMResponse(text=f"Это время уже занято. Могу предложить: {offer}.")
            return LLMResponse(text="Что-то пошло не так с записью, передаю администратору.")
        if name == "cancel_my_last_booking":
            if data.get("ok"):
                return LLMResponse(text="Отменил(а) вашу запись. Будем ждать в другой раз!")
            return LLMResponse(text="Активных записей не нашлось. Хотите записаться заново?")
        if name == "get_client_profile":
            prof = data.get("profile") or {}
            who = f", {prof.get('name')}" if prof.get("name") else ""
            return LLMResponse(text=f"Здравствуйте{who}! Рады слышать вас. Чем помочь?")
        return LLMResponse(text=IDLE)

    def _find_service(self, text: str) -> str | None:
        # ищем пересечение слов названия услуги в тексте пользователя
        # стеммы падежей: «стрижку» → «стрижк», «окрашивание» → «окрас»
        for candidate in ("стрижка женская", "стрижка мужская", "стрижк", "маникюр", "окрас"):
            if candidate in text:
                return candidate
        return None
