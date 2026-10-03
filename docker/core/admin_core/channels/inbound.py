"""Реестр входящих сообщений: защита от повторной доставки.

Каналы (Meta, VK, будущий вебхук сайта) при сбое повторяют доставку. Без
фиксации внешнего id агент ответил бы клиенту дважды и мог создать вторую
бронь. Запись делается ДО долгой обработки — тогда повтор уходит тихо.
"""

from __future__ import annotations

from typing import Any


async def claim(pool: Any, channel: str, external_id: str, preview: str = "") -> bool:
    """Занять внешний id. True — обрабатывать, False — это повтор.

    ``preview`` хранится усечённым и только для разбора инцидента: полный текст
    живёт в диалоге и удаляется по сроку хранения ПДн.
    """
    inserted = await pool.fetchval(
        "INSERT INTO inbound_messages (channel, external_id, preview) "  # noqa: S608 — канал из кода
        "VALUES ($1, $2, $3) ON CONFLICT (channel, external_id) DO NOTHING "
        "RETURNING id",
        channel, external_id, preview[:120],
    )
    return inserted is not None


async def link(pool: Any, channel: str, external_id: str, salon_id: str) -> None:
    """Записать салон для разбора: в какой точке пришло сообщение."""
    await pool.execute(
        "UPDATE inbound_messages SET salon_id = $3 "  # noqa: S608 — канал из кода
        "WHERE channel = $1 AND external_id = $2",
        channel, external_id, salon_id,
    )


async def seen(pool: Any, channel: str, external_id: str) -> bool:
    """Проверить, обрабатывалось ли уже это сообщение (для проверок и метрик)."""
    return bool(await pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM inbound_messages "  # noqa: S608
        "WHERE channel = $1 AND external_id = $2)",
        channel, external_id,
    ))
