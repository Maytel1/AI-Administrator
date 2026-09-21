"""Общий протокол LLM-провайдера. Провайдеры: fake | yandexgpt | openai_compat."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMProvider:
    """messages: [{"role": system|user|assistant|tool, "content": str, ("name" для tool)}]."""

    name = "base"

    async def chat(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> LLMResponse:
        raise NotImplementedError
