"""Выбор периода в отчёте и сравнение с прошлым периодом."""

from datetime import datetime
from zoneinfo import ZoneInfo

from admin_core.web import reports

TZ = ZoneInfo("Asia/Yekaterinburg")
SALON = {
    "id": "11111111-1111-1111-1111-111111111111", "name": "Салон", "plan": "trial",
    "crm_provider": "internal", "timezone": "Asia/Yekaterinburg",
}
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=TZ)


# ═══ 1. РАЗБОР ПЕРИОДА ═════════════════════════════════════════════════════


def test_default_period_is_thirty_days():
    current, _previous = reports.resolve_period(None, TZ, today=NOW)
    assert current.key == "30"
    assert (current.end - current.start).days == 31  # 30 дней назад + сегодня


def test_known_periods_resolve():
    for key, days in (("7", 8), ("30", 31), ("90", 91)):
        current, _prev = reports.resolve_period(key, TZ, today=NOW)
        assert current.key == key
        assert current.days == days


def test_unknown_period_falls_back_to_default():
    """Битая ссылка не должна ронять раздел и не должна молча показывать 7 дней."""
    for junk in ["", "abc", "1000000", "'; DROP TABLE x; --", "30 OR 1=1"]:
        current, _prev = reports.resolve_period(junk, TZ, today=NOW)
        assert current.key == "30", junk


def test_month_period_starts_on_first_day():
    current, previous = reports.resolve_period("month", TZ, today=NOW)
    assert current.start == datetime(2026, 10, 1, tzinfo=TZ).astimezone(ZoneInfo("UTC"))
    assert current.days == 15  # 1-е → завтра (15-е) включительно
    assert previous.end == current.start  # предыдущий период кончается там же


def test_previous_period_is_equal_length_and_adjacent():
    for key in ("7", "30", "90", "month"):
        current, previous = reports.resolve_period(key, TZ, today=NOW)
        assert previous.days == current.days, key
        assert previous.end == current.start, key
        assert previous.start < current.start, key


def test_period_bounds_are_converted_to_utc():
    """Границы считаем в таймзоне салона, в БД уходит UTC."""
    current, _prev = reports.resolve_period("7", TZ, today=NOW)
    assert current.start.tzinfo == ZoneInfo("UTC")
    assert current.end.tzinfo == ZoneInfo("UTC")
    local_start = current.start.astimezone(TZ)
    assert (local_start.hour, local_start.minute) == (0, 0)


# ═══ 2. РАСЧЁТ ИЗМЕНЕНИЙ ════════════════════════════════════════════════════


def test_delta_growth_and_fall():
    up = reports.delta(120.0, 100.0)
    assert up["change"] == 20.0 and up["percent"] == 20.0
    down = reports.delta(80.0, 100.0)
    assert down["change"] == -20.0 and down["percent"] == -20.0


def test_delta_from_zero_has_no_percent():
    """«+100 %» от нуля — это ошибка деления, а не результат."""
    value = reports.delta(1000.0, 0.0)
    assert value["percent"] is None
    assert value["change"] == 1000.0


def test_delta_equal_periods_is_zero():
    value = reports.delta(500.0, 500.0)
    assert value["change"] == 0.0 and value["percent"] == 0.0


async def _book(pool, salon_id, *, days_ago: int):
    """Запись на ``days_ago`` дней назад — чтобы периоды можно было сравнить.

    Пишем напрямую в БД: бот умеет бронировать только будущее, а отчёту нужны
    записи в прошлом. Всё остальное (клиент, услуга, мастер) — из фикстуры.
    """
    import datetime as dt

    from admin_core.crm.internal import InternalCRM

    local_tz = ZoneInfo("Asia/Yekaterinburg")
    stamp = abs(hash((str(salon_id), days_ago))) % 10_000_000
    client = await InternalCRM(pool).ensure_client(
        str(salon_id), f"+7900{stamp:07d}", "Клиент"
    )
    starts = dt.datetime.now(local_tz) - dt.timedelta(days=days_ago)
    starts = starts.replace(hour=11, minute=0, second=0, microsecond=0)
    await pool.execute(
        "INSERT INTO bookings (salon_id, client_id, master_id, service_id, "
        "starts_at, ends_at, status, source, created_by) "
        "SELECT $1::uuid, $2::uuid, m.id, s.id, $3::timestamptz, "
        "        $3::timestamptz + interval '60 minutes', "
        "'completed', 'bot', 'test' "
        "FROM masters m, services s "
        "WHERE m.salon_id = $1::uuid AND s.salon_id = $1::uuid LIMIT 1",
        salon_id, client["id"], starts.astimezone(ZoneInfo("UTC")),
    )


async def test_compare_periods_sees_two_windows(demo, pool):
    """Записи внутри и вне периода попадают в разные окна — сравнение не врёт."""
    salon_id = str(demo["salon"]["id"])
    current, previous = reports.resolve_period("7", TZ)
    await _book(pool, salon_id, days_ago=1)     # текущий период
    await _book(pool, salon_id, days_ago=9)     # прошлый период

    result = await reports.compare_periods(pool, salon_id, current, previous)
    assert result["current"]["funnel"]["bookings"] >= 1
    assert result["previous"]["funnel"]["bookings"] >= 1
    assert result["diff"]["revenue"]["current"] > 0
    assert result["diff"]["revenue"]["previous"] > 0
    assert result["current"]["window"].key == current.key
    assert result["previous"]["window"].days == current.days


async def test_booking_outside_both_windows_is_ignored(demo, pool):
    """Запись двухмесячной давности не попадает ни в текущий, ни в прошлый период."""
    salon_id = str(demo["salon"]["id"])
    current, previous = reports.resolve_period("7", TZ)
    await _book(pool, salon_id, days_ago=80)
    result = await reports.compare_periods(pool, salon_id, current, previous)
    assert result["diff"]["revenue"]["current"] == 0.0
    assert result["diff"]["revenue"]["previous"] == 0.0


# ═══ 4. СТРАНИЦА ОТЧЁТОВ С ПЕРЕКЛЮЧАТЕЛЕМ ════════════════════════════════════


def _page(period="30"):
    from admin_core.web.pages_lists import page_reports

    window, previous = reports.resolve_period(period, TZ, today=NOW)
    data = {
        "period_key": window.key,
        "periods": list(reports.PERIODS),
        "window": window,
        "previous_window": previous,
        "by_day": [{"day": datetime(2026, 10, 10), "revenue": 2000.0, "bookings": 2,
                    "from_bot": 1}],
        "load": [{"name": "Мария", "bookings": 2, "busy_hours": 1.5, "revenue": 2000.0}],
        "current": {
            "funnel": {"revenue": 2000.0, "bookings": 2, "dialogs": 5, "conversion": 40.0,
                       "avg_check": 1000.0, "new_clients": 1, "llm_cost": 0.02,
                       "completed": 1},
            "loss": {"total": 2, "cancelled": 0, "no_show": 0, "lost_rate": 0.0},
        },
        "previous": {
            "funnel": {"revenue": 1000.0, "bookings": 1, "dialogs": 5, "conversion": 20.0,
                       "avg_check": 1000.0, "new_clients": 0, "llm_cost": 0.02,
                       "completed": 1},
            "loss": {"total": 1, "cancelled": 0, "no_show": 0, "lost_rate": 0.0},
        },
        "diff": {
            "revenue": reports.delta(2000.0, 1000.0),
            "bookings": reports.delta(2, 1),
            "dialogs": reports.delta(5, 5),
            "conversion": reports.delta(40.0, 20.0),
            "avg_check": reports.delta(1000.0, 1000.0),
            "lost_rate": reports.delta(0.0, 0.0),
            "new_clients": reports.delta(1, 0),
        },
    }
    data["funnel"] = data["current"]["funnel"]
    data["loss"] = data["current"]["loss"]
    return page_reports(data, SALON, [SALON])
def test_report_page_has_period_tabs():
    """Переключатель периода на месте, активный помечен, салон сохраняется."""
    html = _page()
    assert 'class="tabs"' in html
    for key, label, _days in reports.PERIODS:
        assert f"period={key}" in html
        assert label in html
    assert "tab-active" in html
    assert f"salon={SALON['id']}" in html


def test_report_page_shows_trend_under_tiles():
    """Под плитками — изменение к прошлому периоду, а не просто число."""
    html = _page()
    assert "+100.0 % к прошлому" in html   # выручка выросла вдвое
    assert "trend up" in html
    assert "0.0 % к прошлому" in html      # средний чек не изменился
    assert "trend flat" in html
    assert "не с чем сравнить" in html      # новых клиентов: 1 против 0


def test_report_page_has_comparison_table():
    """Отдельная таблица «сейчас / было / изменение» — цифры, а не проценты."""
    html = _page()
    assert "Сравнение с прошлым периодом" in html
    assert "Показатель" in html and "Сейчас" in html and "Изменение" in html
    assert "Выручка" in html and "Конверсия" in html
    assert "прошлый период (31 дн.)" in html


def test_report_page_period_switch_changes_label():
    """Подпись периода меняется вместе с выбором."""
    assert "7 дней" in _page("7")
    assert "Этот месяц" in _page("month")


def test_report_page_escapes_period_input():
    """Мусорный период из URL не может прийти в HTML как разметка."""
    from admin_core.web.pages_lists import page_reports

    window, previous = reports.resolve_period(None, TZ, today=NOW)
    data = {
        "by_day": [], "load": [], "window": window, "previous_window": previous,
        "period_key": "<script>alert(1)</script>",
        "periods": [("<script>", "<b>период</b>", 7)],
        "funnel": {"revenue": 0.0, "bookings": 0, "dialogs": 0, "conversion": 0.0,
                   "avg_check": 0.0, "new_clients": 0, "llm_cost": 0.0},
        "loss": {"total": 0, "cancelled": 0, "no_show": 0, "lost_rate": 0.0},
    }
    html = page_reports(data, SALON, [SALON])
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;b&gt;период&lt;/b&gt;" in html


if __name__ == "__main__":  # pragma: no cover
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
