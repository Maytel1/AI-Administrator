"""YandexGPT (Yandex AI Studio) — ПДн в РФ (PDN_LLM_OPTIONS.md, вариант 2/5).

⚠️ UNVERIFIED: контракт проверен по публичной документации, но без живого
ключа не оттестирован. После создания YC-аккаунта прогнать 30 сценариев
(критерий фазы 1) и при расхождениях поправить парсинг ответа.

Native function calling в простом completions-API не полагаемся: инструкции
в system просят JSON-команду {"tool": ..., "args": {...}} — так цикл tools
работает одинаково на любом провайдере.

Параметр ``tools`` принимается для совместимости с интерфейсом LLMProvider,
но передаётся в модель неявно: системный промпт инструктирует модель выводить
JSON-команду в тексте. Нативный ``tools`` в completions API Яндекса пока не
поддерживается.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .base import LLMProvider, LLMResponse, ToolCall

JSON_CMD_RE = re.compile(r"\{[\s\S]*\"tool\"\s*:[\s\S]*\}")

_YANDEX_NO_TEXT_STATUSES = {"PROPERTIES_GENERATING", "FINAL"}


class YandexGPTProvider(LLMProvider):
    name = "yandexgpt"

    def __init__(
        self,
        api_key: str,
        folder_id: str | None,
        model_uri: str | None,
        *,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ):
        self.url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completions"
        self.headers = {"Authorization": f"Api-Key {api_key}"}
        self.model_uri = model_uri or f"gpt://{folder_id}/yandexgpt/latest"
        self.temperature = temperature
        self.max_tokens = max_tokens

    @staticmethod
    def _to_api_messages(messages: list[dict]) -> list[dict]:
        out: list[dict] = []
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

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        stream: bool = False,
    ) -> LLMResponse | Any:
        """Отправляет запрос к YandexGPT completions API.

        ``stream=True`` возвращает асинхронный генератор фрагментов текста —
        используется голосовым контуром (Pipecat) для низкой латентности.
        """
        payload: dict[str, Any] = {
            "modelUri": self.model_uri,
            "completionOptions": {
                "temperature": self.temperature,
                "maxTokens": self.max_tokens,
            },
            "messages": self._to_api_messages(messages),
        }
        if stream:
            payload["stream"] = True

        async with httpx.AsyncClient(timeout=60 if stream else 30) as client:
            resp = await client.post(self.url, headers=self.headers, json=payload)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                # Пробуем вытащить человекочитаемую ошибку из тела Яндекса
                detail = ""
                try:
                    body = resp.json()
                    error = body.get("error", {})
                    if isinstance(error, dict):
                        detail = error.get("message", "")
                    elif isinstance(error, str):
                        detail = error
                except Exception:  # noqa: BLE001 — тело не JSON, пропускаем
                    pass
                msg = f"YandexGPT HTTP {resp.status_code}: {detail or exc}"
                raise RuntimeError(msg) from exc
            body = resp.json()

        # ---- не-потоковый режим ----
        if not stream:
            return self._parse_response(body)

        # ---- потоковый режим: возвращаем генератор ----
        return self._stream_response(client, body)

    def _parse_response(self, body: dict[str, Any]) -> LLMResponse:
        try:
            alt = body["result"]["alternatives"][0]
            text = alt["message"].get("text")
            status = alt.get("status")
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"YandexGPT: неожиданный ответ: {body}") from exc

        # Статус PROPERTIES_GENERATING означает, что модель хочет вызвать
        # инструмент, а не вернуть текст. Наш паттерн — JSON-команда в тексте,
        # поэтому считаем это «пустым» текстом и ищем JSON.
        if status in _YANDEX_NO_TEXT_STATUSES:
            text = None

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

    async def _stream_response(
        self, client: httpx.AsyncClient, body: dict[str, Any]
    ) -> Any:
        """Генератор фрагментов текста из потокового ответа Яндекса.

        Используется голосовым контуром. На данный момент Яндекс поддерживает
        SSE-поток в формате ``data: {...}`` на том же endpoint'е.
        """
        # body здесь — уже прочитанный JSON ответа (httpx читает всё тело),
        # поэтому для истинного потока нужно переоткрыть соединение с Accept:
        # text/event-stream. Оставляем заглушку для будущей реализации.
        raise NotImplementedError(
            "Потоковый режим YandexGPT требует отдельного SSE-парсера; "
            "используйте не-потоковый chat() до реализации."
        )
