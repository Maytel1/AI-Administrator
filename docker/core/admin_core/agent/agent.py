"""Агент: цикл «LLM → tools → LLM», история в памяти, ПДн маскируются на входе."""

import json

from ..anonymize import anonymize_for_llm
from ..llm.base import LLMProvider
from .prompt import PROMPT_TEMPLATE
from .tools import TOOL_SPECS, build_dispatch

MAX_TOOL_ROUNDS = 6
HISTORY_LIMIT = 16  # последних сообщений, уходим от бесконтрольного роста контекста

FALLBACK_REPLY = "Извините, повторите, пожалуйста?"
CLARIFY_REPLY = (
    "Уточните, пожалуйста, на какую услугу записать — перечислю цены и свободное время."
)
# Некоторые модели вместо разбора tool_call отдают его текстом — клиенту
# такое показывать нельзя, поэтому режем мусор и отвечаем по-человечески.
_TECH_JUNK = ('{"', "{'", '"type": "function"', '"type":"function"')


def clean_reply(text: str | None) -> str:
    """Ответ клиенту: без пустот, без сырых вызовов инструментов."""
    if not text or not text.strip():
        return FALLBACK_REPLY
    stripped = text.strip()
    if stripped.startswith(_TECH_JUNK):
        return "Уточните, пожалуйста, что именно вас интересует — помогу с записью."
    return text


class AgentSession:
    def __init__(self, crm, provider: LLMProvider, salon: dict, client: dict,
                 created_by: str = "bot:cli"):
        self.crm = crm
        self.provider = provider
        self.salon = salon
        self.client = client
        self.created_by = created_by
        self.dispatch = build_dispatch(
            crm, str(salon["id"]), str(client["id"]), created_by=created_by
        )
        base_prompt = PROMPT_TEMPLATE.format(
            salon_name=salon.get("name", "салон"),
            client_name=client.get("name") or "",
        )
        self.history: list[dict] = [{"role": "system", "content": base_prompt}]
        self.last_offered: list[dict] = []  # предложенные слоты (для create_booking)

    async def run_turn(self, user_text: str) -> str:
        masked = anonymize_for_llm(user_text, self.client.get("name"))
        self.history.append({"role": "user", "content": masked})

        reply = ""
        seen_calls: set[str] = set()
        for _ in range(MAX_TOOL_ROUNDS):
            response = await self.provider.chat(self.history, tools=TOOL_SPECS)
            if response.tool_calls:
                repeated = False
                for tc in response.tool_calls:
                    key = f"{tc.name}:{json.dumps(tc.arguments, sort_keys=True, ensure_ascii=False)}"
                    repeated = repeated or key in seen_calls
                    seen_calls.add(key)
                self.history.append(
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            [tc.__dict__ for tc in response.tool_calls], ensure_ascii=False
                        ),
                    }
                )
                for tc in response.tool_calls:
                    result = await self.dispatch(tc.name, tc.arguments)
                    if tc.name == "get_free_slots":
                        self.last_offered = result.get("slots", [])
                    self.history.append(
                        {
                            "role": "tool",
                            "name": tc.name,
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )
                if repeated:
                    # модель повторяет один и тот же вызов: ходить по кругу бессмысленно,
                    # спрашиваем клиента сами — так диалог не повисает
                    reply = CLARIFY_REPLY
                    break
                continue
            reply = clean_reply(response.text)
            break
        else:
            reply = "Передаю вас старшему администратору, одну минуту."  # защита от зацикливания

        self.history.append({"role": "assistant", "content": reply})
        self._trim()
        return reply

    def _trim(self) -> None:
        if len(self.history) > HISTORY_LIMIT + 1:  # +1 системный промпт
            self.history = [self.history[0]] + self.history[-HISTORY_LIMIT:]
