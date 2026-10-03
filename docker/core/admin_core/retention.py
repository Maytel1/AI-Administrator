"""Хранение персональных данных: 90 дней, потом автоматическое удаление.

Зачем это: мы теперь умеем стирать данные по запросу клиента, но без правила
хранения через год база переписок растёт сама, и объяснить, почему она такая,
нечем. Правило одно и простое: **содержание диалога живёт 90 дней**.

Что именно удаляется:
* текст сообщений диалога старше срока — обнуляется, факт диалога остаётся;
* аудио и транскрипт (`audio_url`, `transcript`) — без права на хранение;
* журнал входов в панель — через 180 дней (это не ПДн клиента, но e-mail);
* истёкшие сессии панели — чистит сам веб-сервис.

Что НЕ удаляется: карточки клиентов, записи, аудит действий. Они нужны для
расчёта выручки и разбора споров, и не содержат содержания переписки.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

RETENTION_DAYS = 90
LOGIN_LOG_RETENTION_DAYS = 180


async def purge_personal_data(
    pool: Any, *, days: int = RETENTION_DAYS, dry_run: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Обнулить содержание старых диалогов. Возвращает, что и сколько затронуто."""
    moment = now or datetime.now(ZoneInfo("UTC"))
    cutoff = moment - timedelta(days=days)

    dialogs = await pool.fetchval(
        "SELECT count(*) FROM dialog_messages m "
        "JOIN dialogs d ON d.id = m.dialog_id "
        "WHERE d.started_at < $1 AND m.content <> '[удалено по сроку хранения]'",
        cutoff,
    )
    media = await pool.fetchval(
        "SELECT count(*) FROM dialogs WHERE started_at < $1 "
        "AND (audio_url IS NOT NULL OR transcript IS NOT NULL)",
        cutoff,
    )
    login_rows = await pool.fetchval(
        "SELECT count(*) FROM login_log WHERE ts < $1",
        moment - timedelta(days=LOGIN_LOG_RETENTION_DAYS),
    )
    stats = {
        "cutoff": cutoff.isoformat(),
        "dialog_messages": int(dialogs or 0),
        "media_rows": int(media or 0),
        "login_log": int(login_rows or 0),
        "dry_run": dry_run,
    }
    if dry_run:
        return stats

    # Содержание заменяем заглушкой, а не удаляем строку: в диалоге остаётся
    # видно, что общение было, и это нужно для разбора жалоб.
    await pool.execute(
        "UPDATE dialog_messages m SET content = '[удалено по сроку хранения]' "
        "FROM dialogs d WHERE d.id = m.dialog_id AND d.started_at < $1 "
        "AND m.content <> '[удалено по сроку хранения]'",
        cutoff,
    )
    await pool.execute(
        "UPDATE dialogs SET audio_url = NULL, transcript = NULL WHERE started_at < $1",
        cutoff,
    )
    await pool.execute(
        "DELETE FROM login_log WHERE ts < $1",
        moment - timedelta(days=LOGIN_LOG_RETENTION_DAYS),
    )
    await pool.execute(
        "INSERT INTO audit_log (actor, action, details) VALUES "
        "('system:retention', 'purge_personal_data', $1)",
        json.dumps(stats, ensure_ascii=False),
    )
    return stats


async def retention_report(pool: Any, *, days: int = RETENTION_DAYS) -> dict[str, Any]:
    """Сколько ПДн сейчас хранится и что будет удалено завтра."""
    cutoff = datetime.now(ZoneInfo("UTC")) - timedelta(days=days)
    total = await pool.fetchval(
        "SELECT count(*) FROM dialog_messages m JOIN dialogs d ON d.id = m.dialog_id "
        "WHERE m.content <> '[удалено по сроку хранения]'"
    )
    expired = await pool.fetchval(
        "SELECT count(*) FROM dialog_messages m JOIN dialogs d ON d.id = m.dialog_id "
        "WHERE d.started_at < $1 AND m.content <> '[удалено по сроку хранения]'",
        cutoff,
    )
    oldest = await pool.fetchval("SELECT min(started_at) FROM dialogs")
    return {
        "retention_days": days,
        "alive_messages": int(total or 0) - int(expired or 0),
        "expired_messages": int(expired or 0),
        "oldest_dialog": oldest,
    }
