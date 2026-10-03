"""Бухгалтерские выгрузки: реестр счетов, долги, клиенты, защита от формул.

Проверяем то, на чём держится сверка: суммы сходятся, реестр отражает оплату,
а значение, начинающееся с «=», не выполняется Excel'ем как формула.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from admin_core.billing import issue_invoice, mark_paid
from admin_core.exports import (
    clients_csv,
    clients_registry,
    debtors,
    payments_csv,
    payments_registry,
    to_csv,
)


def _salon(pool, demo, *, plan: str = "start") -> str:
    salon_id = str(demo["salon"]["id"])
    return salon_id


async def _paid_salon(pool, demo) -> str:
    """Салон с выставленным и оплаченным счётом за текущий месяц."""
    salon_id = _salon(pool, demo)
    await pool.execute(
        "UPDATE salons SET plan = $2, is_active = true WHERE id = $1", salon_id, "start"
    )
    salon = dict(await pool.fetchrow(
        "SELECT id, name, plan, paid_until, timezone FROM salons WHERE id = $1", salon_id
    ))
    invoice = await issue_invoice(pool, salon)
    await mark_paid(pool, salon_id, invoice["payment_id"])
    return salon_id


async def test_payments_registry_totals_match_rows(demo, pool) -> None:
    """Суммы в шапке равны сумме строк: бухгалтерия сверяет именно это."""
    await _paid_salon(pool, demo)
    registry = await payments_registry(pool)
    assert registry["totals"]["count"] == len(registry["rows"])
    issued = sum(float(r["amount"]) for r in registry["rows"])
    assert registry["totals"]["issued"] == pytest.approx(issued, abs=0.01)
    assert registry["totals"]["debt"] == pytest.approx(
        issued - registry["totals"]["paid"], abs=0.01
    )


async def test_payments_csv_has_header_and_row(demo, pool) -> None:
    """В CSV есть шапка и строка счёта с ИНН плательщика."""
    await _paid_salon(pool, demo)
    registry = await payments_registry(pool)
    payload = payments_csv(registry).decode("utf-8-sig")
    lines = [line for line in payload.splitlines() if line]
    assert lines[0].startswith("Счёт;")
    assert len(lines) >= 2
    assert "Сумма" in lines[0]


async def test_csv_escapes_formula_prefix(demo, pool) -> None:
    """Значение, начинающееся с «=», не должно стать формулой в Excel."""
    salon_id = str(demo["salon"]["id"])
    await pool.execute(
        "UPDATE clients SET name = $2 WHERE salon_id = $1",
        salon_id, "=HYPERLINK(\"http://evil\")",
    )
    clients = await clients_registry(pool, salon_id=salon_id)
    payload = clients_csv(clients).decode("utf-8-sig")
    assert "'=HYPERLINK" in payload
    assert "\n=HYPERLINK" not in payload


async def test_debtors_lists_unpaid(demo, pool) -> None:
    """Салон с просроченным счётом попадает в список должников с суммой долга."""
    salon_id = str(demo["salon"]["id"])
    past = (date.today() - timedelta(days=40)).replace(day=1)
    await pool.execute(
        "UPDATE salons SET plan = $2, is_active = true, paid_until = $3 WHERE id = $1",
        salon_id, "start", past,
    )
    await pool.execute(
        "INSERT INTO payments (salon_id, plan, period_start, period_end, amount, "
        "method, status, invoice_no) "
        "VALUES ($1, 'start', $2, $3, 1990, 'sbp', 'issued', 'test-due')",
        salon_id, past, past + timedelta(days=31),
    )
    rows = await debtors(pool)
    mine = [r for r in rows if str(r["id"]) == salon_id]
    assert mine, "просроченный счёт должен попасть в должники"
    assert float(mine[0]["debt"]) == pytest.approx(1990.0, abs=0.01)
    assert mine[0]["oldest_due"] is not None


async def test_debtors_skips_trial(demo, pool) -> None:
    """Пробный период долгом не считается."""
    salon_id = str(demo["salon"]["id"])
    await pool.execute(
        "UPDATE salons SET plan = 'trial', is_active = true WHERE id = $1", salon_id
    )
    rows = await debtors(pool)
    assert all(str(r["id"]) != salon_id for r in rows)


async def test_clients_registry_counts_visits(demo, pool) -> None:
    """В реестре клиентов видно визиты и неявки."""
    salon_id = str(demo["salon"]["id"])
    client = dict(await pool.fetchrow(
        "SELECT id, phone FROM clients WHERE salon_id = $1 LIMIT 1", salon_id
    ))
    now = datetime.now(ZoneInfo("UTC"))
    for status in ("booked", "no_show"):
        await pool.execute(
            "INSERT INTO bookings (salon_id, client_id, service_id, master_id, "
            "starts_at, ends_at, status, source, created_by) "
            "SELECT $1, $2, s.id, m.id, $4::timestamptz, "
            "       $4::timestamptz + interval '60 min', $3, 'bot', 'bot' "
            "FROM services s, masters m WHERE s.salon_id = $1 AND m.salon_id = $1 LIMIT 1",
            salon_id, client["id"], status,
            now.replace(hour=12, minute=0, second=0, microsecond=0),
        )
    rows = await clients_registry(pool, salon_id=salon_id)
    mine = [r for r in rows if r["phone"] == client["phone"]]
    assert mine and mine[0]["visits"] >= 1 and mine[0]["no_shows"] >= 1


def test_to_csv_uses_bom_and_semicolons() -> None:
    """BOM и разделитель «;» — иначе Excel открывает файл нечитаемым."""
    payload = to_csv(["Колонка", "Значение"], [["первая", "вторая"]])
    assert payload.startswith(b"\xef\xbb\xbf")
    text = payload.decode("utf-8-sig")
    assert "Колонка;Значение" in text
    assert "первая;вторая" in text


async def test_payments_registry_period_is_current_month(demo, pool) -> None:
    """Период по умолчанию — текущий календарный месяц."""
    registry = await payments_registry(pool)
    today = date.today()
    assert f"{today:%m.%Y}" in registry["period"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
