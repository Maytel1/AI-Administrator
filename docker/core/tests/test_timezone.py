"""Часовой пояс проекта: БД и приложение должны считать в UTC+5.

Проверка ловит то, из-за чего счёт выставлялся не в тот день: worker считал
по своему TZ, а БД жила в UTC, и на стыке суток «сегодня» расходилось.
"""


import pytest

from admin_core.db import DB_TIMEZONE, db_timezone


def test_default_timezone_is_ekaterinburg() -> None:
    """По умолчанию UTC+5 без перехода на летнее время."""
    assert DB_TIMEZONE == "Asia/Yekaterinburg"
    assert db_timezone() == "Asia/Yekaterinburg"


def test_timezone_takes_precedence_from_env(monkeypatch) -> None:
    """Переопределение из окружения работает: салоны могут быть в другой зоне."""
    monkeypatch.setenv("DB_TIMEZONE", "Asia/Almaty")
    assert db_timezone() == "Asia/Almaty"


def test_broken_timezone_fails_loudly(monkeypatch) -> None:
    """Опечатка в TZ должна падать сразу, а не молча ломать все даты."""
    monkeypatch.setenv("DB_TIMEZONE", "Екатеринбург")
    with pytest.raises(RuntimeError, match="часовым поясом"):
        db_timezone()


async def test_pool_runs_in_configured_timezone(pool) -> None:
    """Живое соединение отвечает в agreed-таймзоне, а не в UTC."""
    from datetime import datetime  # noqa: PLC0415
    from zoneinfo import ZoneInfo  # noqa: PLC0415

    name = await pool.fetchval("SHOW TIMEZONE")
    assert name == db_timezone()
    # Дата в БД обязана совпадать с календарной датой в этой же зоне: иначе
    # автосчёт выставляет счёт не в тот день.
    db_date = await pool.fetchval("SELECT CURRENT_DATE")
    local_date = datetime.now(ZoneInfo(db_timezone())).date()
    assert db_date == local_date, (
        f"БД живёт в {name}, сегодня по Екатеринбургу {local_date}, "
        f"а CURRENT_DATE = {db_date}"
    )
if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
