"""Сквозной сценарий: AgentSession + FakeProvider + живая БД (без внешних ключей)."""

from admin_core.agent.agent import AgentSession
from admin_core.llm.fake import FakeProvider


async def test_booking_flow(pool, demo):
    session = AgentSession(
        demo["crm"], FakeProvider(), demo["salon"], demo["client"], created_by="bot:test"
    )
    r1 = await session.run_turn("Хочу записаться на стрижку")
    assert "Свободно" in r1
    assert session.last_offered, "агент получил слоты через tools"

    when = session.last_offered[0]["start_text"].split(" ")[-1]  # "HH:MM"
    r2 = await session.run_turn(when)
    assert "Готово" in r2

    n = await pool.fetchval(
        "SELECT count(*) FROM bookings WHERE client_id = $1 AND status = 'booked'",
        demo["client"]["id"],
    )
    assert n == 1


async def test_robot_question_answered_honestly(pool, demo):
    """Решение 20.09 (вопросы 8/11/12): на прямой вопрос — честный ответ."""
    session = AgentSession(demo["crm"], FakeProvider(), demo["salon"], demo["client"])
    reply = await session.run_turn("Вы робот?")
    assert "цифровой ассистент" in reply


async def test_cancel_flow(pool, demo):
    session = AgentSession(demo["crm"], FakeProvider(), demo["salon"], demo["client"])
    await session.run_turn("Хочу записаться на стрижку")
    when = session.last_offered[0]["start_text"].split(" ")[-1]
    await session.run_turn(when)

    reply = await session.run_turn("Отмените мою запись")
    assert "Отменил" in reply

    n = await pool.fetchval(
        "SELECT count(*) FROM bookings WHERE client_id = $1 AND status = 'cancelled'",
        demo["client"]["id"],
    )
    assert n == 1


async def test_phone_not_leaked_to_llm_history(pool, demo):
    """PDN_LLM_OPTIONS.md: в историю для LLM попадает обезличенный текст."""
    session = AgentSession(demo["crm"], FakeProvider(), demo["salon"], demo["client"])
    await session.run_turn("Мой телефон +79001234567, запишите меня на стрижку")
    history_text = " ".join(m["content"] for m in session.history if m["role"] == "user")
    assert "+79001234567" not in history_text
    assert "[ТЕЛЕФОН]" in history_text
