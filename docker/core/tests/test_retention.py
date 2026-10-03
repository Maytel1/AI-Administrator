"""Политика хранения ПДн: 90 дней, содержание обнуляется, факт остаётся.

Проверяем именно то, что обещает retention.py: старые диалоги очищаются,
свежие не трогаются, аудио и журнал входов исчезают, повторный прогон безопасен.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from admin_core.retention import (
    LOGIN_LOG_RETENTION_DAYS,
    RETENTION_DAYS,
    purge_personal_data,
    retention_report,
)


async def _dialog(pool, salon_id: str, chat_id: int, days_ago: float, text: str) -> str:
    """Диалог с сообщением клиента: chat_id в схеме текстовый, канал обязателен."""
    moment = datetime.now(ZoneInfo("UTC")) - timedelta(days=days_ago)
    dialog_id = await pool.fetchval(
        "INSERT INTO dialogs (salon_id, chat_id, channel, started_at) "
        "VALUES ($1, $2, 'telegram', $3) RETURNING id",
        salon_id, str(chat_id), moment,
    )
    await pool.execute(
        "INSERT INTO dialog_messages (dialog_id, role, content) VALUES ($1, 'user', $2)",
        dialog_id, text,
    )
    return str(dialog_id)


async def test_purge_wipes_content_of_old_dialogs(demo, pool):
    """Содержание старых диалогов обнуляется, а сами диалоги остаются."""
    salon_id = str(demo["salon"]["id"])
    old_id = await _dialog(pool, salon_id, 7001, RETENTION_DAYS + 5, "давно сказанное")
    new_id = await _dialog(pool, salon_id, 7002, RETENTION_DAYS - 5, "свежий разговор")
    stats = await purge_personal_data(pool)
    assert stats["dialog_messages"] == 1, "обнулиться должен ровно один диалог"
    assert await pool.fetchval(
        "SELECT content FROM dialog_messages WHERE dialog_id = $1", old_id
    ) == "[удалено по сроку хранения]"
    assert await pool.fetchval(
        "SELECT content FROM dialog_messages WHERE dialog_id = $1", new_id
    ) == "свежий разговор"
    assert await pool.fetchval("SELECT count(*) FROM dialogs WHERE id = $1", old_id) == 1


async def test_purge_removes_audio_transcript_and_login_log(demo, pool):
    """Аудио, транскрипт и журнал входов удаляются по сроку хранения."""
    salon_id = str(demo["salon"]["id"])
    old = datetime.now(ZoneInfo("UTC")) - timedelta(days=RETENTION_DAYS + 1)
    dialog_id = await pool.fetchval(
        "INSERT INTO dialogs (salon_id, chat_id, channel, started_at, "
        "audio_url, transcript) VALUES ($1, '42', 'voice', $2, '/audio/x.ogg', 'текст') "
        "RETURNING id",
        salon_id, old,
    )
    await pool.execute(
        "INSERT INTO login_log (login, ts, ok) VALUES ('old@test.ru', $1, false)",
        datetime.now(ZoneInfo("UTC")) - timedelta(days=LOGIN_LOG_RETENTION_DAYS + 1),
    )
    await purge_personal_data(pool)
    row = await pool.fetchrow(
        "SELECT audio_url, transcript FROM dialogs WHERE id = $1", dialog_id
    )
    assert row["audio_url"] is None and row["transcript"] is None
    assert await pool.fetchval(
        "SELECT count(*) FROM login_log WHERE login = 'old@test.ru'"
    ) == 0, "старые записи журнала входов удаляются"


async def test_purge_dry_run_changes_nothing_and_second_pass_is_empty(demo, pool):
    """dry_run ничего не делает, повторный прогон не считает старые строки снова."""
    salon_id = str(demo["salon"]["id"])
    dialog_id = await _dialog(pool, salon_id, 7003, RETENTION_DAYS + 2, "ещё раз")
    preview = await purge_personal_data(pool, dry_run=True)
    assert preview["dialog_messages"] == 1 and preview["dry_run"] is True
    assert await pool.fetchval(
        "SELECT content FROM dialog_messages WHERE dialog_id = $1", dialog_id
    ) == "ещё раз"
    assert (await purge_personal_data(pool))["dialog_messages"] == 1
    assert (await purge_personal_data(pool))["dialog_messages"] == 0


async def test_purge_leaves_audit_trail(demo, pool):
    """Автоматическое удаление попадает в аудит — иначе его нечем подтвердить."""
    salon_id = str(demo["salon"]["id"])
    await _dialog(pool, salon_id, 7004, RETENTION_DAYS + 2, "под аудит")
    await purge_personal_data(pool)
    details = await pool.fetchval(
        "SELECT details FROM audit_log WHERE action = 'purge_personal_data' "
        "ORDER BY ts DESC LIMIT 1"
    )
    assert details and "dialog_messages" in details


async def test_retention_report_splits_alive_and_expired(demo, pool):
    """Отчёт разделяет живые данные и те, что ждут удаления."""
    salon_id = str(demo["salon"]["id"])
    await _dialog(pool, salon_id, 7005, RETENTION_DAYS + 3, "старое")
    await _dialog(pool, salon_id, 7006, 2, "свежее")
    report = await retention_report(pool)
    assert report["retention_days"] == RETENTION_DAYS
    assert report["expired_messages"] >= 1
    assert report["alive_messages"] >= 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
