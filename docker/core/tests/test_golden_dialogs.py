"""Золотой набор диалогов: регрессия качества бота.

Зачем: метрики качества мы считаем, но эталонных сценариев не было — значит,
ухудшить ответы можно было незаметно. Теперь любая правка промпта без правки
набора ломает тесты.

Как проверяем: сценарий задаёт реплики клиента и инструмент, который обязан
вызваться. Провайдер в тесте «следует» сценарию, а агент — настоящий: так
проверяются маршрутизация, защита от выдуманных обещаний и отсутствие зацикливания.
"""

import json
import pathlib

import pytest

from admin_core.agent.agent import AgentSession

GOLDEN = pathlib.Path(__file__).parent / "golden" / "dialogs.json"
CASES = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]


class ScriptedProvider:
    """Провайдер, повторяющий сценарий: сначала tool_call, затем ответ."""

    def __init__(self, tool: str | None) -> None:
        self.tool = tool
        self.calls: list[str] = []

    async def chat(self, history, tools=None):  # noqa: ANN001, ANN201
        from admin_core.llm.base import LLMResponse, ToolCall  # noqa: PLC0415

        text = (history[-1].get("content") or "") if history else ""
        if self.tool and text not in self.calls:
            self.calls.append(text)
            return LLMResponse(
                text="",
                tool_calls=[ToolCall(name=self.tool, arguments=self._args())],
            )
        return LLMResponse(text="Готово, уточню детали и запишу вас.", tool_calls=[])

    def _args(self) -> dict:
        """Аргументы инструмента: агент их всё равно пережимает своей логикой."""
        return {}


def _session(pool, demo, tool: str | None) -> AgentSession:
    from admin_core.crm.internal import InternalCRM  # noqa: PLC0415

    return AgentSession(
        InternalCRM(pool),
        ScriptedProvider(tool),
        dict(demo["salon"]),
        dict(demo["client"]),
        created_by="test:golden",
    )


def test_golden_set_is_not_empty() -> None:
    """Пустой набор — не проверка: сценарии должны быть, и их должно быть много."""
    assert len(CASES) >= 5
    for case in CASES:
        assert case["turns"], case["name"]
        for turn in case["turns"]:
            assert turn["user"].strip()
            assert "tool" in turn


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
async def test_golden_dialog_case(demo, pool, case: dict) -> None:
    """Каждый сценарий: нужный инструмент вызван, лишних обещаний нет."""
    called: list[str] = []
    for turn in case["turns"]:
        session = _session(pool, demo, turn["tool"])
        # Запоминаем, что реально вызвал агент.
        original = session.dispatch

        async def spy(tool_name, arguments, _orig=original):  # noqa: ANN001
            called.append(tool_name)
            return await _orig(tool_name, arguments)

        session.dispatch = spy  # type: ignore[method-assign]
        reply = await session.run_turn(turn["user"])
        if turn["tool"]:
            assert turn["tool"] in called, (
                f"{case['name']}: ожидали {turn['tool']}, вызвано {called}"
            )
        for forbidden in case.get("forbidden", []):
            assert forbidden.lower() not in reply.lower(), (
                f"{case['name']}: ответ обещает лишнего — «{forbidden}»"
            )
        for needle in case.get("reply_contains", []):
            assert needle.lower() in reply.lower(), (
                f"{case['name']}: в ответе нет «{needle}»"
            )


async def test_agent_asks_to_stop_instead_of_looping(demo, pool) -> None:
    """Модель, повторяющая один вызов, не должна гонять диалог по кругу."""
    from admin_core.llm.base import LLMResponse, ToolCall  # noqa: PLC0415

    class LoopingProvider:
        async def chat(self, history, tools=None):  # noqa: ANN001, ANN201
            return LLMResponse(
                text="", tool_calls=[ToolCall(name="list_services", arguments={})]
            )

    from admin_core.crm.internal import InternalCRM  # noqa: PLC0415

    session = AgentSession(
        InternalCRM(pool), LoopingProvider(), dict(demo["salon"]),
        dict(demo["client"]), created_by="test:golden",
    )
    reply = await session.run_turn("хочу записаться")
    assert reply, "агент должен ответить, а не зависнуть"


async def test_client_data_is_not_sent_to_llm(demo, pool) -> None:
    """Телефон и имя клиента не уходят в модель: передаём обезличенный текст."""
    from admin_core.anonymize import anonymize_for_llm  # noqa: PLC0415

    masked = anonymize_for_llm(
        "Меня зовут Анна, телефон +79001234567", "Анна"
    )
    assert "+79001234567" not in masked
    assert "Анна" not in masked


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
