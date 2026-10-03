"""Биллинг: ручной счёт + СБП по реквизитам, тарифы помесячные.

Схема выбрана под ответ клиента: салон платит напрямую, помесячно, карты не
храним. Значит автосписания нет, а платёж подтверждает менеджер — и система
должна честно показывать, кто оплачен, а кто нет.

Что делает модуль:
* считает сумму по тарифу за месяц (цены — в env, не в коде);
* выдаёт период ``[начало, конец)`` помесячной оплаты;
* формирует текст счёта с реквизитами и суммой прописью;
* помечает оплату и двигает ``salons.paid_until``;
* отвечает на вопрос «действует ли тариф сейчас».
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

MONTHS_RU = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)
ONES = {
    0: "ноль", 1: "один", 2: "два", 3: "три", 4: "четыре", 5: "пять",
    6: "шесть", 7: "семь", 8: "восемь", 9: "девять", 10: "десять",
}
TEENS = {
    10: "десять", 11: "одиннадцать", 12: "двенадцать", 13: "тринадцать",
    14: "четырнадцать", 15: "пятнадцать", 16: "шестнадцать",
    17: "семнадцать", 18: "восемнадцать", 19: "девятнадцать",
}
TENS = {
    20: "двадцать", 30: "тридцать", 40: "сорок", 50: "пятьдесят",
    60: "шестьдесят", 70: "семьдесят", 80: "восемьдесят", 90: "девяносто",
}
# Разряды: сотни и тысячи считаются словами, а не «девятьсот» из цифр.
HUNDREDS_ONES = {
    1: "сто", 2: "двести", 3: "триста", 4: "четыреста", 5: "пятьсот",
    6: "шестьсот", 7: "семьсот", 8: "восемьсот", 9: "девятьсот",
}
THOUSAND_ONES = {1: "одна", 2: "две", 3: "три", 4: "четыре"}

# Цены по умолчанию (₽/месяц). Переопределяются BILLING_PRICES (JSON): цены
# меняются по договорённости, и трогать код для этого не должно.
DEFAULT_PRICES: dict[str, int] = {
    "trial": 0, "start": 1990, "standard": 3990, "pro": 7990,
}


def prices() -> dict[str, int]:
    """Цены тарифов: из env, иначе дефолтные."""
    raw = os.getenv("BILLING_PRICES", "").strip()
    out = dict(DEFAULT_PRICES)
    if not raw:
        return out
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return out
    for plan, value in parsed.items():
        try:
            out[str(plan)] = int(value)
        except (TypeError, ValueError):
            continue
    return out


def price_of(plan: str) -> int:
    """Сумма за месяц по тарифу."""
    return int(prices().get(plan, 0))


def requisites() -> dict[str, str]:
    """Реквизиты для счёта и СБП — из env, чтобы не зашивать в код."""
    keys = (
        "BILLING_LEGAL_NAME", "BILLING_INN", "BILLING_KPP", "BILLING_BANK",
        "BILLING_BIK", "BILLING_ACCOUNT", "BILLING_TG", "BILLING_EMAIL",
    )
    return {key.removeprefix("BILLING_").lower(): os.getenv(key, "") for key in keys}


@dataclass(frozen=True)
class Period:
    """Помесячный период оплаты: полуоткрытый интервал [start, end)."""

    start: date
    end: date

    @property
    def label(self) -> str:
        return f"{MONTHS_RU[self.start.month - 1]} {self.start.year}"


def next_period(start: date | None = None) -> Period:
    """Следующий месячный период: с 1-го числа на месяц вперёд.

    Привязка к 1-му числу, а не «+30 дней»: иначе периоды разъезжаются и оплата
    «за месяц» перестаёт означать календарный месяц.
    """
    if start is None:
        today = date.today()
        start = date(today.year, today.month, 1)
    if start.day != 1:
        start = date(start.year, start.month, 1)
    end_year, end_month = (
        (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    )
    return Period(start, date(end_year, end_month, 1))


def next_period_from(paid_until: Any) -> Period:
    """Период после уже оплаченного."""
    if paid_until is None:
        return next_period()
    end = paid_until if isinstance(paid_until, date) else date.fromisoformat(
        str(paid_until)[:10]
    )
    return next_period(end if end.day == 1 else end + timedelta(days=1))
def amount_to_words(amount: float) -> str:
    """Сумма прописью для счёта: банк и бухгалтерия это требуют.

    Раскладываем по разрядам (тысячи/сотни/десятки/единицы): наивное деление
    на сотни ломается на 1990 — «девятнадцать сот» вместо «тысяча девятьсот».
    """
    rubles = int(amount)
    kopecks = int(round((amount - rubles) * 100))
    words: list[str] = []
    if rubles == 0:
        words.append("ноль")
    else:
        thousands, rest = divmod(rubles, 1000)
        if thousands:
            words.append(_thousands(thousands))
        words.extend(_under_thousand(rest))
    head = " ".join(words)
    if rubles % 10 == 1 and rubles % 100 != 11:
        suffix = "рубль"
    elif rubles % 100 in (11, 12, 13, 14):
        suffix = "рублей"
    elif rubles % 10 in (2, 3, 4):
        suffix = "рубля"
    else:
        suffix = "рублей"
    return f"{head} {suffix} {kopecks:02d} копеекки"


def _under_thousand(value: int) -> list[str]:
    """Слова для числа 1..999: сотни, десятки и единицы по разрядам."""
    words: list[str] = []
    hundreds, rest = divmod(value, 100)
    tens, ones = divmod(rest, 10)
    if hundreds:
        words.append(HUNDREDS_ONES[hundreds])
    if tens == 1:
        # 11–14 читаются одним словом: TEENS ключуется по полному числу.
        words.append(TEENS[10 + ones])
    elif tens:
        # TENS ключуется по полному числу десятков: 2 -> «двадцать».
        words.append(TENS[tens * 10])
        if ones:
            words.append(ONES[ones])
    elif ones:
        words.append(ONES[ones])
    return words


def _thousands(value: int) -> str:
    """1 → «одна тысяча», 21 → «двадцать одна тысяча», 7 → «семь тысяч»."""
    if value == 1:
        return "одна тысяча"
    head = " ".join(_under_thousand(value))
    tail = value % 10
    if tail == 1 and value % 100 != 11:
        # «один тысяча» — грамматически неверно: перед «тысяча» нужно «одна».
        head = head[:-len("один")] + "одна" if head.endswith("один") else head + "одна"
        ending = "тысяча"
    elif tail in (2, 3, 4):
        ending = "тысячи"
    else:
        ending = "тысяч"
    return f"{head} {ending}"


def invoice_text(salon: dict[str, Any], period: Period, amount: int, number: str) -> str:
    """Текст счёта: его можно отправить салону как есть."""
    req = requisites()
    who = str(salon.get("name") or "Клиент")
    lines = [
        f"СЧЁТ № {number} от {date.today():%d.%m.%Y}",
        "",
        f"Плательщик: {who}",
        f"Тариф: {salon.get('plan')}, месячное обслуживание",
        f"Период: {period.label} ({period.start:%d.%m.%Y} — {period.end:%d.%m.%Y})",
        f"Сумма: {amount} руб.",
        f"Сумма прописью: {amount_to_words(amount)}",
        "Оплата: перевод по реквизитам (СБП). Карты не храним, автосписания нет.",
        "",
        "Реквизиты для перевода:",
        f"  Получатель: {req['legal_name']}" if req["legal_name"]
        else "  Получатель: (задайте BILLING_LEGAL_NAME)",
        f"  ИНН {req['inn']}" if req["inn"] else "",
        f"  КПП {req['kpp']}" if req["kpp"] else "",
        f"  Банк: {req['bank']}" if req["bank"] else "",
        f"  БИК {req['bik']}" if req["bik"] else "",
        f"  Р/с {req['account']}" if req["account"] else "",
        f"  Назначение: оплата за {period.label}, счёт № {number}",
    ]
    if req["tg"]:
        lines.extend(["", f"Вопросы по оплате: {req['tg']}"])
    return "\n".join(line for line in lines if line)


async def issue_invoice(
    pool: Any, salon: dict[str, Any], *, actor: str = "team:cli",
) -> dict[str, Any]:
    """Выставить счёт за следующий месячный период."""
    salon_id = str(salon["id"])
    period = next_period_from(salon.get("paid_until"))
    amount = price_of(str(salon.get("plan") or "start"))
    number = f"{period.start:%Y%m}-{str(salon['id'])[:4].upper()}"
    row = await pool.fetchrow(
        "INSERT INTO payments (salon_id, plan, period_start, period_end, amount, "
        "reference, invoice_no, created_by) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
        "ON CONFLICT (salon_id, period_start) DO UPDATE "
        "SET plan = EXCLUDED.plan, amount = EXCLUDED.amount, "
        "invoice_no = EXCLUDED.invoice_no "
        # xmax = 0 бывает только у реально вставленной строки: так автосчёт
        # отличает новый счёт от того же самого, найденного за период.
        "RETURNING id, status, amount, (xmax = 0) AS created",
        salon_id, salon.get("plan"), period.start, period.end, amount,
        f"Оплата за {period.label}, счёт {number}", number, actor,
    )
    return {
        "ok": True,
        "payment_id": str(row["id"]),
        "created": bool(row["created"]),
        "status": row["status"],
        "period": period,
        "amount": int(row["amount"]),
        "invoice_no": number,
        "text": invoice_text(salon, period, int(row["amount"]), number),
    }


async def mark_paid(
    pool: Any, salon_id: str, payment_id: str, *,
    actor: str = "team:cli", note: str = "",
) -> dict[str, Any]:
    """Отметить счёт оплаченным и продлить оплаченный период."""
    row = await pool.fetchrow(
        "UPDATE payments SET status = 'paid', paid_at = now(), note = $3 "
        "WHERE id = $2 AND salon_id = $1 AND status <> 'paid' "
        "RETURNING period_start, period_end, amount",
        salon_id, payment_id, note,
    )
    if not row:
        return {"ok": False, "error": "not_found"}
    await pool.execute(
        "UPDATE salons SET paid_until = GREATEST(COALESCE(paid_until, $2), $2) "
        "WHERE id = $1",
        salon_id, row["period_end"],
    )
    await pool.execute(
        "INSERT INTO audit_log (actor, action, details) VALUES "
        "('system:billing', 'payment_paid', $1)",
        json.dumps(
            {"payment_id": payment_id, "amount": float(row["amount"]),
             "period_end": str(row["period_end"])},
            ensure_ascii=False,
        ),
    )
    return {"ok": True, "period_end": row["period_end"], "amount": float(row["amount"])}


async def billing_status(pool: Any, salon: dict[str, Any]) -> dict[str, Any]:
    """Состояние оплаты точки: что показывать в панели и в тревогах."""
    salon_id = str(salon["id"])
    paid_until = salon.get("paid_until")
    paid_until_date: date | None = None
    if paid_until is not None:
        paid_until_date = paid_until if isinstance(paid_until, date) else date.fromisoformat(
            str(paid_until)[:10]
        )
    latest = await pool.fetchrow(
        "SELECT period_start, period_end, amount, status, invoice_no, paid_at "
        "FROM payments WHERE salon_id = $1 ORDER BY period_start DESC LIMIT 1",
        salon_id,
    )
    expired = paid_until_date is not None and paid_until_date < date.today()
    # Итоги месяца по сети: бухгалтерия спрашивает «сколько должны» раньше всего.
    totals = await pool.fetchrow(
        "SELECT coalesce(sum(amount) FILTER (WHERE status = 'paid'), 0) AS paid,"
        "       coalesce(sum(amount) FILTER (WHERE status = 'issued'), 0) AS debt "
        "FROM payments WHERE period_start >= date_trunc('month', CURRENT_DATE)"
    )
    return {
        "plan": salon.get("plan"),
        "paid_amount": float(totals["paid"] or 0),
        "debt": float(totals["debt"] or 0),
        "price": price_of(str(salon.get("plan") or "start")),
        "paid_until": paid_until_date,
        "expired": expired,
        "is_trial": str(salon.get("plan")) == "trial",
        "latest": dict(latest) if latest else None,
        "requisites": requisites(),
        "status_text": _status_text(salon, paid_until_date, expired),
    }

async def auto_invoice_tick(pool: Any, *, today_day: int | None = None) -> dict[str, Any]:

    """Выставить счета салонам, у которых сегодня день выставления.

    Дата берётся из карточки плательщика (``invoice_due_day``), а не «1-го
    числа»: у салонов разный график оплаты, и счёт должен приходить в их
    день. За один период счёт выставляется один раз — это гарантирует
    уникальный индекс в ``payments``.

    Логика живёт в ядре, а не в worker: её нужно тестировать и запускать из
    CLI при ручной проверке.
    """
    day = today_day or date.today().day
    rows = await pool.fetch(
        "SELECT l.salon_id, l.invoice_due_day, s.name, s.plan, s.paid_until, "
        "s.timezone FROM client_legal l JOIN salons s ON s.id = l.salon_id "
        "WHERE l.auto_invoice AND s.is_active AND s.plan <> 'trial'"
    )
    issued: list[str] = []
    errors: list[str] = []
    for row in rows:
        if day != int(row["invoice_due_day"]):
            continue
        salon = dict(row)
        # issue_invoice ждёт salon["id"], а выборка отдаёт salon_id: без этой
        # строки автосчёт падал бы с KeyError на каждом салоне.
        salon["id"] = str(row["salon_id"])
        salon["timezone"] = str(salon.get("timezone") or "UTC")
        try:
            invoice = await issue_invoice(pool, salon, actor="system:auto")
        except Exception as exc:  # noqa: BLE001 — один салон не ломает цикл
            errors.append(f"{salon.get('name')}: {type(exc).__name__}: {exc}")
            continue
        # Счёт за период может уже существовать: считаем только новые,
        # иначе каждый тик worker'а «выставлял» бы один и тот же счёт.
        if invoice.get("created"):
            issued.append(str(invoice["payment_id"]))
    return {"issued": issued, "errors": errors, "day": day}


def _status_text(salon: dict[str, Any], paid_until: date | None, expired: bool) -> str:
    """Человеческий текст состояния оплаты для панели и тревог."""
    plan = str(salon.get("plan") or "start")
    if plan == "trial":
        return "Пробный период: работает бесплатно"
    if paid_until is None:
        return "Тариф не оплачен — выставьте счёт"
    if expired:
        return f"Оплата просрочена: последний платёж до {paid_until:%d.%m.%Y}"
    return f"Тариф «{plan}» оплачен до {paid_until:%d.%m.%Y}"
