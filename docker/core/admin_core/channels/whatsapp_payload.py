"""Разбор входящего вебхука Meta: все сообщения, без потерь и дублей.

Meta присылает пачками: несколько сообщений в одном `value.messages` и несколько
`change` внутри `entry`. Раньше сервис брал только первое — остальные клиенту
никто не отвечал, и он думал, что его не услышали.

Второе: при сбое доставки Meta повторяет webhook. Поэтому `extract` отдаёт
`wamid` каждого сообщения, а решать «обрабатывать или уже обработано» должен
вызывающий — по таблице ``inbound_messages``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

STATUS_TYPES = {"delivered", "read", "failed"}


@dataclass(frozen=True)
class InboundMessage:
    """Одно входящее сообщение клиента."""

    wamid: str
    phone: str
    text: str
    kind: str = "text"


def extract(payload: dict[str, Any]) -> list[InboundMessage]:
    """Все текстовые сообщения из payload — в порядке возрастания времени.

    Статусы доставки (``statuses``) пропускаются: это наши собственные ответы,
    а не сообщения клиента. Сообщения без ``wamid`` пропускаем тоже — без него
    нельзя защититься от повторной доставки, а повтор хуже, чем молчание.
    """
    found: list[tuple[str, InboundMessage]] = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value") or {}
            for message in value.get("messages", []) or []:
                text = (message.get("text") or {}).get("body", "").strip()
                wamid = str(message.get("id") or "").strip()
                phone = str(message.get("from") or "").strip()
                if not text or not wamid or not phone:
                    continue
                if message.get("type") not in (None, "text"):
                    # Голосовые и картинки пока не разбираем, но и не теряем молча:
                    # помечаем типом, чтобы в диалоге был виден факт сообщения.
                    text = text or "[вложение]"
                stamp = str(message.get("timestamp") or "")
                found.append((stamp, InboundMessage(wamid, phone, text)))
    found.sort(key=lambda item: item[0])
    return [message for _stamp, message in found]


def is_duplicate(known_ids: set[str], wamid: str) -> bool:
    """Повторная доставка: тот же wamid уже обработан."""
    return wamid in known_ids
