"""OpenAI-совместимый провайдер (gpt-4o-mini или любой совместимый эндпоинт).

⚠️ UNVERIFIED без ключа. Для РФ ключи обычно через посредника (см.
TARIFFS.md/PDN_LLM_OPTIONS.md): OPENAI_BASE_URL можно указать на прокси.
ПДн клиентов наружу НЕ отправляем — на вход агенту идёт обезличенный текст
(PDN_LLM_OPTIONS.md, вариант 1).
"""

import json

import httpx

from .base import LLMProvider, LLMResponse, ToolCall


class OpenAICompatProvider(LLMProvider):
    name = "openai_compat"

    def __init__(self, api_key: str, base_url: str, model: str):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.model = model

    async def chat(self, messages: list[dict], tools=None) -> LLMResponse:
        payload: dict = {"model": self.model, "messages": messages, "temperature": 0.3}
        if tools:
            payload["tools"] = [
                {"type": "function", "function": {"name": t["name"],
                                                 "description": t.get("description", ""),
                                                 "parameters": t.get("parameters", {"type": "object", "properties": {}})}}
                for t in tools
            ]
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(self.url, headers=self.headers, json=payload)
            resp.raise_for_status()
            body = resp.json()
        msg = body["choices"][0]["message"]
        calls = [
            ToolCall(name=tc["function"]["name"],
                     arguments=json.loads(tc["function"]["arguments"] or "{}"))
            for tc in msg.get("tool_calls", [])
        ]
        return LLMResponse(text=msg.get("content"), tool_calls=calls)
