"""YandexGPT (Yandex AI Studio) — ПДн в РФ (PDN_LLM_OPTIONS.md, вариант 2/5).

⚠️ UNVERIFIED: контракт проверен по публичной документации, но без живого
ключа не оттестирован. После создания YC-аккаунта прогнать 30 сценариев
(критерий фазы 1) и при расхождениях поправить парсинг ответа.

Native function calling в простом completions-API не полагаемся: инструкции
в system просят JSON-команду {"tool": ..., "args": {...}} — так цикл tools
работает одинаково на любом провайдере.
"""

import json
import re

import httpx

from .base import LLMProvider, LLMResponse, ToolCall

JSON_CMD_RE = re.compile(r"\{[\s\S]*\"tool\"\s*:[\s\S]*\}")


class YandexGPTProvider(LLMProvider):
    name = "yandexgpt"

    def __init__(self, api_key: str, folder_id: str | None, model_uri: str | None):
        self.url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completions"
        self.headers = {"Authorization": f"Api-Key {api_key}"}
        self.model_uri = model_uri or f"gpt://{folder_id}/yandexgpt/latest"

    @staticmethod
    def _to_api_messages(messages: list[dict]) -> list[dict]:
        out = []
        for m in messages:
            if m["role"] == "tool":
                out.append(
                    {
                        "role": "user",
                        "text": f"Результат инструмента `{m.get('name', '')}`: {m['content']}",
                    }
                )
            else:
                out.append({"role": m["role"], "text": m["content"]})
        return out

    async def chat(self, messages: list[dict], tools=None) -> LLMResponse:
        payload = {
            "modelUri": self.model_uri,
            "completionOptions": {"temperature": 0.3, "maxTokens": 800},
            "messages": self._to_api_messages(messages),
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(self.url, headers=self.headers, json=payload)
            resp.raise_for_status()
            body = resp.json()
        try:
            alt = body["result"]["alternatives"][0]
            text = alt["message"]["text"]
            if alt.get("status") == "PROPERTIES_GENERATING":
                text = None  # модель хочет вызвать инструмент
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"YandexGPT: неожиданный ответ: {body}") from exc

        if text:
            match = JSON_CMD_RE.search(text)
            if match:
                try:
                    cmd = json.loads(match.group(0))
                    cleaned = JSON_CMD_RE.sub("", text).strip()
                    return LLMResponse(
                        tool_calls=[
                            ToolCall(name=cmd["tool"], arguments=cmd.get("args", {}))
                        ],
                        text=cleaned or None,
                    )
                except (json.JSONDecodeError, KeyError):
                    pass
            return LLMResponse(text=text)
        return LLMResponse(text=None)
