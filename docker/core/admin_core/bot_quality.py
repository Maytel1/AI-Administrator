"""Качество работы бота: конверсия в запись, уходы к человеку, длина диалога.

Зачем: владелец видит выручку, но не видит главного про свою главную фичу —
насколько бот полезен. Без этих чисел «бот работает» остаётся мнением, а после
неудачного разговора это мнение будет неверным.

Метрики за период:
* ``conversion`` — доля диалогов, закончившихся записью (созданной в диалоге);
* ``escalation`` — доля диалогов, где клиент попросил человека;
* ``avg_turns`` — среднее число сообщений клиента: слишком много — бот зациклился,
  слишком мало — вероятно, не понял запрос;
* ``bookings_via_bot`` — сколько записей пришло именно через бота.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

ESCALATION_WORDS = (
    "человек", "оператор", "администратор", "живой", "телефон", "позвоните",
    "не хочу бот", "живого человека", "переключи",
)


def looks_like_escalation(content: str) -> bool:
    """Просит ли клиент живого человека."""
    text = content.lower()
    return any(word in text for word in ESCALATION_WORDS)


async def bot_quality(
    pool: Any, salon_id: str, start: datetime, end: datetime
) -> dict[str, Any]:
    """Считает метрики качества бота за период по диалогам салона."""
    dialogs = await pool.fetch(
        "SELECT d.id, d.client_id, d.started_at, d.ended_at, "
        "(SELECT count(*) FROM dialog_messages m "
        "  WHERE m.dialog_id = d.id AND m.role = 'user') AS user_msgs, "
        "(SELECT count(*) FROM bookings b "
        "  WHERE b.client_id = d.client_id AND b.created_by = 'bot' "
        "    AND b.starts_at BETWEEN $2 AND $3) AS bookings "
        "FROM dialogs d WHERE d.salon_id = $1 AND d.started_at >= $2 "
        "AND d.started_at < $3",
        salon_id, start, end,
    )
    escalated = await pool.fetchval(
        "SELECT count(DISTINCT m.dialog_id) FROM dialog_messages m "
        "JOIN dialogs d ON d.id = m.dialog_id "
        "WHERE d.salon_id = $1 AND d.started_at >= $2 AND d.started_at < $3 "
        "AND m.role = 'user' AND lower(m.content) LIKE ANY($4)",
        salon_id, start, end, [f"%{w}%" for w in ESCALATION_WORDS],
    )
    total = len(dialogs)
    with_booking = sum(1 for d in dialogs if d["bookings"] > 0)
    turns = [int(d["user_msgs"]) for d in dialogs]
    escalations = int(escalated or 0)
    return {
        "dialogs": total,
        "dialogs_with_booking": with_booking,
        "conversion": round(with_booking / total * 100, 1) if total else 0.0,
        "escalations": escalations,
        "escalation_rate": round(escalations / total * 100, 1) if total else 0.0,
        "avg_turns": round(sum(turns) / len(turns), 1) if turns else 0.0,
        "bookings_via_bot": sum(int(d["bookings"]) for d in dialogs),
    }
