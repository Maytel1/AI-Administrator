"""Карточка плательщика: ИП, самозанятый или ООО + автовыставление счёта.

Зачем по типам: набор полей принципиально разный. Самозанятому банк просит ФИО
и ИНН, ИП — ещё ОГРНИП и адрес, ООО — КПП, ОГРН, директора и банковский счёт.
Форма показывает только нужные поля, а проверка контрольных сумм отсекает
ошибку в ИНН до выставления счёта: банк отвергнет такой платёж, и деньги
потеряются по дороге.

Контрольные суммы считаем сами: ошибка в одной цифре неочевидна и стоит
звонка в бухгалтерию.
"""

from __future__ import annotations

from typing import Any

CLIENT_TYPES: dict[str, str] = {
    "self_employed": "Самозанятый (НПД)",
    "ip": "ИП",
    "llc": "ООО / юридическое лицо",
}

# Поля по типу плательщика: (ключ, подпись, обязательное ли, подсказка).
FIELDS: dict[str, list[tuple[str, str, bool, str]]] = {
    "self_employed": [
        ("npd_rate", "Ставка НПД", False, "4 % или 6 %"),
    ],
    "ip": [
        ("ogrnip", "ОГРНИП (15 цифр)", True, "Из выписки ЕГРИП"),
        ("kpp", "КПП (9 цифр)", False, "Только при наличии обособленного подразделения"),
        ("address", "Адрес регистрации", True, "Индекс, адрес"),
        ("okved", "Основной ОКВЭД", False, "Например: 96.02"),
        ("bank", "Банк (если счёт в банке)", False, ""),
        ("bik", "БИК (9 цифр)", False, ""),
        ("account", "Личный счёт (20 цифр)", False, "Можно платить на счёт физлица"),
    ],
    "llc": [
        ("kpp", "КПП (9 цифр)", True, "Из выписки ЕГРЮЛ"),
        ("ogrn", "ОГРН (13 цифр)", True, "Основной государственный регистрационный номер"),
        ("address", "Юридический адрес", True, "Индекс, адрес"),
        ("director", "Руководитель", True, "ФИО и должность"),
        ("bank", "Наименование банка", True, ""),
        ("bik", "БИК (9 цифр)", True, ""),
        ("account", "Расчётный счёт (20 цифр)", True, ""),
        ("okved", "Основной ОКВЭД", False, "Например: 96.02"),
    ],
}


def fields_of(client_type: str) -> list[tuple[str, str, bool, str]]:
    """Поля, показываемые для выбранного типа плательщика."""
    return FIELDS.get(client_type, FIELDS["ip"])


def digits(value: Any) -> str:
    """Только цифры: реквизиты вводят с пробелами и тире."""
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def validate_inn(value: Any) -> bool:
    """ИНН: 10 цифр (юрлицо) или 12 (ИП/физлицо), с контрольными суммами."""
    inn = digits(value)
    if len(inn) == 10:
        weights = (2, 4, 10, 3, 5, 9, 4, 6, 8)
        total = sum(int(inn[i]) * w for i, w in enumerate(weights))
        return total % 11 % 10 == int(inn[9])
    if len(inn) == 12:
        first = sum(
            int(inn[i]) * w for i, w in enumerate((7, 2, 4, 10, 3, 5, 9, 4, 6, 8))
        )
        second = sum(
            int(inn[i]) * w
            for i, w in enumerate((3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8))
        )
        return (
            first % 11 % 10 == int(inn[10]) and second % 11 % 10 == int(inn[11])
        )
    return False


def validate_ogrnip(value: Any) -> bool:
    """ОГРНИП (15 цифр): 15-я цифра — остаток от деления первых 14 на 11."""
    number = digits(value)
    return len(number) == 15 and int(number[:14]) % 11 % 10 == int(number[14])


def validate_ogrn(value: Any) -> bool:
    """ОГРН (13 цифр): 13-я цифра — остаток от деления первых 12 на 11."""
    number = digits(value)
    return len(number) == 13 and int(number[:12]) % 11 % 10 == int(number[12])


def validate_due_day(value: Any) -> int:
    """Дата выставления счёта: 1..28 (29-е в феврале выставлять нельзя)."""
    try:
        day = int(value)
    except (TypeError, ValueError):
        return 1
    return day if 1 <= day <= 28 else 1


def inn_length_ok(client_type: str, value: Any) -> bool:
    """Длина ИНН должна соответствовать типу: у юрлица 10, у ИП и СЗ — 12.

    Проверки контрольной суммы недостаточно: 10-значный ИНН юрлица проходит её,
    но для ИП такой ИНН просто не существует — и счёт уйдёт в банк с чужими
    реквизитами.
    """
    expected = 10 if client_type == "llc" else 12
    return len(digits(value)) == expected


def validate(
    client_type: str, legal_name: str, inn: str, details: dict[str, Any]
) -> list[str]:
    """Список ошибок для человека. Пустой список — сохранять можно."""
    errors: list[str] = []
    if client_type not in CLIENT_TYPES:
        return ["выберите тип плательщика"]
    if not str(legal_name or "").strip():
        errors.append("укажите ФИО или наименование")
    if not inn_length_ok(client_type, inn) or not validate_inn(inn):
        expected = 10 if client_type == "llc" else 12
        errors.append(f"ИНН должен содержать {expected} цифр и проходить проверку")
    for key, title, required, _hint in fields_of(client_type):
        value = str(details.get(key, "")).strip()
        if not value:
            # Необязательное поле без значения — это нормально; проверяем формат
            # только когда человек что-то ввёл: пустой КПП у ИП не ошибка,
            # а КПП из трёх цифр — ошибка.
            if required:
                errors.append(f"заполните «{title}»")
            continue
        if key == "ogrnip" and not validate_ogrnip(value):
            errors.append("ОГРНИП: не сходится контрольная сумма")
        elif key == "ogrn" and not validate_ogrn(value):
            errors.append("ОГРН: не сходится контрольная сумма")
        elif key == "kpp" and len(digits(value)) != 9:
            errors.append("КПП должен содержать 9 цифр")
        elif key == "bik" and len(digits(value)) != 9:
            errors.append("БИК должен содержать 9 цифр")
        elif key == "account" and digits(value) and len(digits(value)) != 20:
            errors.append("счёт должен содержать 20 цифр")
    return errors


async def save(
    pool: Any, salon_id: str, client_type: str, legal_name: str, inn: str,
    details: dict[str, Any], *, due_day: Any = 1, auto_invoice: bool = True,
    contract_no: str = "", contract_date: Any = None,
) -> dict[str, Any]:
    """Сохранить карточку плательщика."""
    import json  # noqa: PLC0415 — только для записи jsonb

    errors = validate(client_type, legal_name, inn, details)
    if errors:
        return {"ok": False, "errors": errors}
    await pool.execute(
        "INSERT INTO client_legal (salon_id, client_type, legal_name, inn, details, "
        "invoice_due_day, auto_invoice, contract_no, contract_date, updated_at) "
        "VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, now()) "
        "ON CONFLICT (salon_id) DO UPDATE SET client_type = EXCLUDED.client_type, "
        "legal_name = EXCLUDED.legal_name, inn = EXCLUDED.inn, "
        "details = EXCLUDED.details, invoice_due_day = EXCLUDED.invoice_due_day, "
        "auto_invoice = EXCLUDED.auto_invoice, contract_no = EXCLUDED.contract_no, "
        "contract_date = EXCLUDED.contract_date, updated_at = now()",
        salon_id, client_type, str(legal_name).strip(), digits(inn),
        json.dumps(details, ensure_ascii=False),
        validate_due_day(due_day), bool(auto_invoice),
        str(contract_no or "").strip() or None, contract_date,
    )
    await pool.execute(
        "INSERT INTO audit_log (actor, action, details) VALUES "
        "('team:billing', 'client_legal_saved', $1)",
        json.dumps({"client_type": client_type, "inn": digits(inn)}, ensure_ascii=False),
    )
    return {"ok": True, "errors": []}


async def get(pool: Any, salon_id: str) -> dict[str, Any] | None:
    """Карточка плательщика или None, если её ещё нет."""
    row = await pool.fetchrow(
        "SELECT client_type, legal_name, inn, details, invoice_due_day, auto_invoice, "
        "contract_no, contract_date, created_at, updated_at "
        "FROM client_legal WHERE salon_id = $1",
        salon_id,
    )
    if not row:
        return None
    data = dict(row)
    # jsonb приезжает строкой без кодека: без разбора details["ogrn"] упало бы.
    if isinstance(data.get("details"), str):
        import json  # noqa: PLC0415

        try:
            data["details"] = json.loads(data["details"])
        except json.JSONDecodeError:
            data["details"] = {}
    return data
