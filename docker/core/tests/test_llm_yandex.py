"""Тесты YandexGPT-провайдера без живого ключа (PDN_LLM_OPTIONS.md, вариант 2/5).

Все HTTP-ответы Яндекса мокаются, поэтому тесты не требуют YC-аккаунта.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from admin_core.llm.yandex import YandexGPTProvider


class _FakeResponse:
    """Имитация httpx.Response с заранее заданным JSON-телом и статусом."""

    def __init__(self, status_code: int, json_body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._json = json_body
        self.text = str(json_body)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=httpx.Request("POST", "x"), response=self
            )

    def json(self) -> dict[str, Any]:
        return self._json


class _FakeClient:
    """Имитация httpx.AsyncClient с очередью заранее заданных ответов."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> _FakeResponse:
        return self._responses.pop(0)


def _ok_body(text: str, status: str | None = None) -> dict[str, Any]:
    alternative: dict[str, Any] = {"message": {"role": "assistant", "text": text}}
    if status:
        alternative["status"] = status
    return {"result": {"alternatives": [alternative]}}


# ---------------------------------------------------------------------------
# Успешные сценарии
# ---------------------------------------------------------------------------


async def test_plain_text_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Обычный текстовый ответ возвращается как есть."""
    monkeypatch.setattr(
        "admin_core.llm.yandex.httpx.AsyncClient",
        lambda *a, **k: _FakeClient([_FakeResponse(200, _ok_body("Здравствуйте!"))]),
    )
    provider = YandexGPTProvider(api_key="test-key", folder_id="test-folder", model_uri=None)
    reply = await provider.chat([{"role": "user", "content": "Привет"}])
    assert reply.text == "Здравствуйте!"
    assert reply.tool_calls == []


async def test_json_command_is_extracted(monkeypatch: pytest.MonkeyPatch) -> None:
    """JSON-команда в тексте извлекается в tool_calls, текст очищается."""
    body = _ok_body(
        'Запишу вас на 15:00. {"tool": "create_booking", "args": {"service": "Стрижка", "time": "15:00"}}'
    )
    monkeypatch.setattr(
        "admin_core.llm.yandex.httpx.AsyncClient",
        lambda *a, **k: _FakeClient([_FakeResponse(200, body)]),
    )
    provider = YandexGPTProvider(api_key="test-key", folder_id="test-folder", model_uri=None)
    reply = await provider.chat([{"role": "user", "content": "Хочу записаться"}])
    assert reply.text == "Запишу вас на 15:00."
    assert len(reply.tool_calls) == 1
    assert reply.tool_calls[0].name == "create_booking"
    assert reply.tool_calls[0].arguments == {"service": "Стрижка", "time": "15:00"}


async def test_empty_text_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если текст пустой, возвращается LLMResponse с text=None."""
    monkeypatch.setattr(
        "admin_core.llm.yandex.httpx.AsyncClient",
        lambda *a, **k: _FakeClient([_FakeResponse(200, _ok_body(""))]),
    )
    provider = YandexGPTProvider(api_key="test-key", folder_id="test-folder", model_uri=None)
    reply = await provider.chat([{"role": "user", "content": "Пусто"}])
    assert reply.text is None
    assert reply.tool_calls == []


# ---------------------------------------------------------------------------
# Роли сообщений
# ---------------------------------------------------------------------------


async def test_tool_role_converted_to_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сообщения с ролью tool конвертируются в user с пометкой инструмента."""
    captured: dict[str, Any] = {}

    async def fake_post(url: str, *, headers: dict[str, str], json: dict[str, Any]) -> _FakeResponse:
        captured["payload"] = json
        return _FakeResponse(200, _ok_body("OK"))

    client = _FakeClient([_FakeResponse(200, _ok_body("OK"))])
    client.post = fake_post  # type: ignore[method-assign]
    monkeypatch.setattr("admin_core.llm.yandex.httpx.AsyncClient", lambda *a, **k: client)

    provider = YandexGPTProvider(api_key="test-key", folder_id="test-folder", model_uri=None)
    await provider.chat(
        [
            {"role": "user", "content": "Запиши меня"},
            {"role": "assistant", "content": "{\"tool\": \"get_free_slots\", \"args\": {}}"},
            {"role": "tool", "name": "get_free_slots", "content": "[11:00, 15:00]"},
        ]
    )
    assert captured["payload"]["messages"][2] == {
        "role": "user",
        "text": "Результат инструмента `get_free_slots`: [11:00, 15:00]",
    }


# ---------------------------------------------------------------------------
# Ошибки HTTP
# ---------------------------------------------------------------------------


async def test_401_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """401 от Яндекса → RuntimeError с текстом ошибки."""
    body = {"error": {"message": "Invalid API key"}}
    monkeypatch.setattr(
        "admin_core.llm.yandex.httpx.AsyncClient",
        lambda *a, **k: _FakeClient([_FakeResponse(401, body)]),
    )
    provider = YandexGPTProvider(api_key="bad-key", folder_id="test-folder", model_uri=None)
    with pytest.raises(RuntimeError, match="YandexGPT HTTP 401"):
        await provider.chat([{"role": "user", "content": "Привет"}])


async def test_500_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """500 от Яндекса → RuntimeError."""
    monkeypatch.setattr(
        "admin_core.llm.yandex.httpx.AsyncClient",
        lambda *a, **k: _FakeClient([_FakeResponse(500, {})]),
    )
    provider = YandexGPTProvider(api_key="test-key", folder_id="test-folder", model_uri=None)
    with pytest.raises(RuntimeError):
        await provider.chat([{"role": "user", "content": "Привет"}])


# ---------------------------------------------------------------------------
# Некорректные ответы
# ---------------------------------------------------------------------------


async def test_missing_alternatives_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ответ без alternatives → RuntimeError."""
    monkeypatch.setattr(
        "admin_core.llm.yandex.httpx.AsyncClient",
        lambda *a, **k: _FakeClient([_FakeResponse(200, {"result": {}})]),
    )
    provider = YandexGPTProvider(api_key="test-key", folder_id="test-folder", model_uri=None)
    with pytest.raises(RuntimeError, match="неожиданный ответ"):
        await provider.chat([{"role": "user", "content": "Привет"}])


async def test_malformed_json_command_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Битый JSON в тексте не ломает парсинг — возвращается текст как есть."""
    monkeypatch.setattr(
        "admin_core.llm.yandex.httpx.AsyncClient",
        lambda *a, **k: _FakeClient(
            [_FakeResponse(200, _ok_body("Текст с битым JSON: {tool: broken}"))]
        ),
    )
    provider = YandexGPTProvider(api_key="test-key", folder_id="test-folder", model_uri=None)
    reply = await provider.chat([{"role": "user", "content": "Привет"}])
    assert reply.text == "Текст с битым JSON: {tool: broken}"
    assert reply.tool_calls == []


# ---------------------------------------------------------------------------
# Параметры запроса
# ---------------------------------------------------------------------------


async def test_request_contains_model_uri_and_options(monkeypatch: pytest.MonkeyPatch) -> None:
    """В теле запроса передаются modelUri, temperature, maxTokens и сообщения."""
    captured: dict[str, Any] = {}

    async def fake_post(url: str, *, headers: dict[str, str], json: dict[str, Any]) -> _FakeResponse:
        captured["payload"] = json
        return _FakeResponse(200, _ok_body("OK"))

    client = _FakeClient([_FakeResponse(200, _ok_body("OK"))])
    client.post = fake_post  # type: ignore[method-assign]
    monkeypatch.setattr("admin_core.llm.yandex.httpx.AsyncClient", lambda *a, **k: client)

    provider = YandexGPTProvider(
        api_key="test-key",
        folder_id="folder-123",
        model_uri="gpt://folder-123/yandexgpt-lite",
        temperature=0.7,
        max_tokens=2048,
    )
    await provider.chat([{"role": "user", "content": "Привет"}])
    payload = captured["payload"]
    assert payload["modelUri"] == "gpt://folder-123/yandexgpt-lite"
    assert payload["completionOptions"]["temperature"] == 0.7
    assert payload["completionOptions"]["maxTokens"] == 2048
    assert payload["messages"][0] == {"role": "user", "text": "Привет"}
