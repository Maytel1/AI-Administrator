"""Вебхук Meta: все сообщения обрабатываются, повторы — нет.

Здесь закрываются две реальные потери: клиент отправлял два сообщения подряд и
получал один ответ, а при повторной доставке от Meta получал второй ответ и
вторую бронь.
"""

import pytest

from admin_core.channels.inbound import claim, link, seen
from admin_core.channels.whatsapp_payload import extract, is_duplicate

PAYLOAD_TWO_MESSAGES = {
    "entry": [{
        "id": "0",
        "changes": [{
            "field": "messages",
            "value": {"messages": [
                {"id": "wamid.A", "from": "79001112233",
                 "timestamp": "1760000002",
                 "text": {"body": "хочу записаться"}},
                {"id": "wamid.B", "from": "79001112233",
                 "timestamp": "1760000001",
                 "text": {"body": "на завтра утром"}},
            ]},
        }],
    }]
}


def test_extract_returns_every_message_not_only_first() -> None:
    """Оба сообщения доходят до обработчика, раньше терялось второе."""
    messages = extract(PAYLOAD_TWO_MESSAGES)
    assert len(messages) == 2
    assert {m.wamid for m in messages} == {"wamid.A", "wamid.B"}
    assert all(m.phone == "79001112233" for m in messages)


def test_messages_are_sorted_by_timestamp() -> None:
    """Порядок сохраняется: клиент может сначала уточнить, потом попросить."""
    messages = extract(PAYLOAD_TWO_MESSAGES)
    assert [m.wamid for m in messages] == ["wamid.B", "wamid.A"]


def test_statuses_are_not_messages() -> None:
    """Статусы доставки — это наши ответы, клиентом они не считаются."""
    payload = {"entry": [{"changes": [{"value": {"statuses": [
        {"id": "wamid.A", "status": "delivered"},
    ]}}]}]}
    assert extract(payload) == []


def test_messages_without_id_are_skipped() -> None:
    """Без wamid нельзя защититься от повтора — молчание лучше дубля ответа."""
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"from": "79001112233", "text": {"body": "без id"}},
    ]}}]}]}
    assert extract(payload) == []


def test_empty_payload_is_not_an_error() -> None:
    """Meta шлёт пустые уведомления о статусах — это норма, а не сбой."""
    assert extract({}) == []
    assert extract({"entry": []}) == []


def test_duplicate_detection() -> None:
    """Повторная доставка распознаётся по wamid."""
    known = {"wamid.A"}
    assert is_duplicate(known, "wamid.A") is True
    assert is_duplicate(known, "wamid.B") is False


async def test_claim_message_is_idempotent(pool) -> None:
    """Второй webhook с тем же wamid не проходит: ни ответа, ни второй брони."""
    assert await claim(pool, "whatsapp", "wamid.X", "первое") is True
    assert await claim(pool, "whatsapp", "wamid.X", "первое") is False
    assert await seen(pool, "whatsapp", "wamid.X") is True


async def test_claim_is_per_channel(pool) -> None:
    """Один и тот же id из разных каналов — разные сообщения."""
    assert await claim(pool, "whatsapp", "same") is True
    assert await claim(pool, "vk", "same") is True


async def test_claim_stores_preview_and_salon(demo, pool) -> None:
    """Превью обрезается: полный текст живёт в диалоге и имеет свой срок."""
    salon_id = str(demo["salon"]["id"])
    assert await claim(pool, "whatsapp", "wamid.P", "x" * 500) is True
    await link(pool, "whatsapp", "wamid.P", salon_id)
    row = await pool.fetchrow(
        "SELECT preview, salon_id FROM inbound_messages WHERE external_id = $1",
        "wamid.P",
    )
    assert len(row["preview"]) == 120
    assert str(row["salon_id"]) == salon_id


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
