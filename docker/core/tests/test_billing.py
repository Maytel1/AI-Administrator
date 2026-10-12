"""Ручной счёт + СБП: периоды, суммы прописью, оплата и её защита.

Проверяем ровно то, на чём держится ручной процесс: помесячный период не
разъезжается, счёт не задваивается, а оплата продлевает подписку ровно на
оплаченный месяц и не выдаётся дважды.
"""

from datetime import date

import pytest
from admin_core.billing import (
    amount_to_words,
    billing_status,
    invoice_text,
    issue_invoice,
    mark_paid,
    next_period,
    next_period_from,
    price_of,
)


def test_next_period_is_calendar_month() -> None:
    """Период — календарный месяц, а не «+30 дней»: иначе месяцы разъезжаются."""
    period = next_period(date(2026, 3, 17))
    assert period.start == date(2026, 3, 1)
    assert period.end == date(2026, 4, 1)


def test_next_period_crosses_year() -> None:
    """Декабрь должен переходить в январь следующего года, а не «теряться»."""
    period = next_period(date(2026, 12, 5))
    assert period.start == date(2026, 12, 1)
    assert period.end == date(2027, 1, 1)


def test_next_period_from_paid_until_continues_without_gap() -> None:
    """После оплаты до 1-го следующий период начинается в тот же день."""
    period = next_period_from(date(2026, 6, 1))
    assert period.start == date(2026, 6, 1)
    assert period.end == date(2026, 7, 1)


def test_next_period_without_payment_starts_this_month() -> None:
    """Нет оплаты — период текущего месяца с 1-го числа."""
    period = next_period_from(None)
    assert period.start.day == 1
    assert (period.end - period.start).days in (28, 29, 30, 31)


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (0, "ноль рублей"),
        (1, "один рубль"),
        (2, "два рубля"),
        (5, "пять рублей"),
        (11, "одиннадцать рублей"),
        (21, "двадцать один рубль"),
        (100, "сто рублей"),
        (101, "сто один рубль"),
        (1990, "одна тысяча девятьсот девяносто рублей"),
        (3990, "три тысячи девятьсот девяносто рублей"),
        (7990, "семь тысяч девятьсот девяносто рублей"),
        # Актуальный прайс (TARIFFS.md, вариант B): 5000/12000/15000.
        (5000, "пять тысяч рублей"),
        # 12/13/14 тысяч склоняются как «тысяч», а не «тысячи» — регрессия.
        (12000, "двенадцать тысяч рублей"),
        (15000, "пятнадцать тысяч рублей"),
        (21000, "двадцать одна тысяча рублей"),
    ],
)
def test_amount_to_words(amount: int, expected: str) -> None:
    """Сумма прописью: банк и бухгалтерия сверяют именно её."""
    assert amount_to_words(amount).startswith(expected)


def test_price_of_unknown_plan_is_zero_not_crash() -> None:
    """Неизвестный тариф не должен ронять выставление счёта."""
    assert price_of("нет-такого") >= 0


def test_default_prices_match_tariffs(monkeypatch) -> None:
    """Дефолт цен = согласованный прайс TARIFFS.md (вариант B).

    При пустом BILLING_PRICES панель берёт цены отсюда. Если они разойдутся с
    прайсом, на встрече с клиентом всплывёт вторая цифра — фиксируем.
    """
    monkeypatch.delenv("BILLING_PRICES", raising=False)
    assert price_of("start") == 5000
    assert price_of("standard") == 12000
    assert price_of("pro") == 15000
    assert price_of("trial") == 0


def test_invoice_text_mentions_period_and_requisites(monkeypatch) -> None:
    """В счёте есть период, сумма и реквизиты — это всё, что нужно клиенту."""
    monkeypatch.setenv("BILLING_LEGAL_NAME", "ИП Иванов И.И.")
    monkeypatch.setenv("BILLING_INN", "123456789012")
    period = next_period(date(2026, 3, 5))
    text = invoice_text(
        {"name": "Салон «Анна»", "plan": "start"}, period, 1990, "202603-ABCD"
    )
    assert "Салон «Анна»" in text
    assert "1990" in text
    assert "202603-ABCD" in text
    assert "март" in text
    assert "ИП Иванов И.И." in text
    assert "123456789012" in text


async def test_issue_invoice_is_idempotent_per_period(demo, pool) -> None:
    """Повторное выставление за тот же месяц не плодит счета."""
    salon = dict(demo["salon"])
    first = await issue_invoice(pool, salon)
    second = await issue_invoice(pool, salon)
    assert first["payment_id"] == second["payment_id"], "счёт за месяц один"
    assert await pool.fetchval(
        "SELECT count(*) FROM payments WHERE salon_id = $1", salon["id"]
    ) == 1


async def test_mark_paid_extends_subscription_once(demo, pool) -> None:
    """Оплата продлевает срок до конца оплаченного месяца и не повторяется."""
    salon_id = str(demo["salon"]["id"])
    await pool.execute(
        "UPDATE salons SET plan = 'start', paid_until = NULL WHERE id = $1", salon_id
    )
    salon = dict(await pool.fetchrow(
        "SELECT id, name, plan, paid_until, timezone FROM salons WHERE id = $1",
        salon_id,
    ))
    invoice = await issue_invoice(pool, salon)
    result = await mark_paid(pool, salon_id, invoice["payment_id"])
    assert result["ok"] is True
    paid_until = await pool.fetchval(
        "SELECT paid_until FROM salons WHERE id = $1", salon_id
    )
    assert paid_until == invoice["period"].end
    again = await mark_paid(pool, salon_id, invoice["payment_id"])
    assert again["ok"] is False, "второй раз оплаченным не отметить"
    status = await billing_status(pool, {**salon, "paid_until": paid_until})
    assert status["expired"] is False
    assert "оплачен до" in status["status_text"]


async def test_billing_status_reports_unpaid_trial(demo, pool) -> None:
    """Новая точка: триал и честное «счёт не выставлен»."""
    status = await billing_status(pool, {**dict(demo["salon"]), "plan": "trial"})
    assert status["is_trial"] is True
    assert status["latest"] is None
    assert "бесплатно" in status["status_text"]


async def test_expired_subscription_is_reported(demo, pool) -> None:
    """Просроченная оплата должна быть видна, а не спрятаться в пустоте."""
    past = date(2020, 1, 1)
    await pool.execute(
        "UPDATE salons SET plan = 'start', paid_until = $2 WHERE id = $1",
        demo["salon"]["id"], past,
    )
    status = await billing_status(pool, {
        **dict(demo["salon"]), "plan": "start", "paid_until": past,
    })
    assert status["expired"] is True
    assert "просрочена" in status["status_text"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
