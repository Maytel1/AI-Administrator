"""Карточка плательщика: тип → поля, контрольные суммы, автосчёт по дате.

Неверный ИНН должен отсекаться: банк всё равно отвергнет такой платёж, но
деньги потеряются по дороге. Поля — только у нужного типа плательщика.
"""

from datetime import date

import pytest

from admin_core.billing_legal import (
    CLIENT_TYPES,
    digits,
    fields_of,
    validate,
    validate_due_day,
    validate_inn,
    validate_ogrn,
    validate_ogrnip,
)

IP_DETAILS = {"ogrnip": "304774600000013", "address": "620000, Екатеринбург, Ленина, 1"}
LLC_DETAILS = {
    "kpp": "667001001", "ogrn": "1027700132195", "address": "101000, Москва",
    "director": "Иванов Иван Иванович, генеральный директор",
    "bank": "ПАО Сбербанк", "bik": "044525225", "account": "40702810900000000001",
}


def test_inn_checksum_accepts_real_shapes() -> None:
    """ИНН физлица (12) и юрлица (10) считаются по контрольной сумме."""
    assert validate_inn("5001007329") is True      # ООО, 10 цифр
    assert validate_inn("123456789047") is True      # ИП/физлицо, 12 цифр
    assert validate_inn("5001007328") is False     # последняя цифра неверна
    assert validate_inn("1234567890") is False       # 10 цифр вместо 12 для ИП


def test_ogrn_checksum() -> None:
    """ОГРН — остаток от деления первых 12 цифр на 11."""
    assert validate_ogrn("1027700132195") is True
    assert validate_ogrn("1027700132196") is False
    assert validate_ogrn("123") is False
    assert validate_ogrnip("123") is False


def test_digits_strips_punctuation() -> None:
    """Реквизиты вводят с пробелами и тире — банк их не принимает."""
    assert digits(" 500-100-7329 ") == "5001007329"
    assert digits(None) == ""


def test_each_type_has_its_own_fields() -> None:
    """Набор полей у типов разный: самозанятому банковский счёт не нужен."""
    self_keys = {f[0] for f in fields_of("self_employed")}
    llc_keys = {f[0] for f in fields_of("llc")}
    ip_keys = {f[0] for f in fields_of("ip")}
    assert "ogrn" in llc_keys and "ogrn" not in ip_keys
    assert "ogrnip" in ip_keys and "ogrnip" not in llc_keys
    assert "bank" not in self_keys
    assert set(CLIENT_TYPES) == {"self_employed", "ip", "llc"}


def test_validate_lists_all_missing_fields() -> None:
    """Пустые обязательные поля перечисляются все, а не по одному за раз."""
    errors = validate("llc", "ООО Ромашка", "5001007329", {})
    assert any("КПП" in e for e in errors)
    assert any("ОГРН" in e for e in errors)
    assert any("Руководитель" in e for e in errors)
    assert validate("ip", "ИП Иванов", "123456789047", IP_DETAILS) == []


def test_validate_rejects_inn_of_wrong_length_for_type() -> None:
    """Копирование ИНН между типами — самая частая ошибка."""
    assert any("ИНН" in e for e in validate("llc", "ООО", "123456789047", LLC_DETAILS))
    assert any("ИНН" in e for e in validate("ip", "ИП", "5001007329", IP_DETAILS))


def test_validate_rejects_bad_ogrnip_and_kpp() -> None:
    """Контрольные суммы и длины проверяются, а не принимаются как есть."""
    errors = validate(
        "ip", "ИП Иванов", "123456789047",
        dict(IP_DETAILS, ogrnip="304774600000014", kpp="123"),
    )
    assert any("ОГРНИП" in e for e in errors)
    assert any("КПП" in e for e in errors)


def test_due_day_is_clamped() -> None:
    """29-е число отбрасываем: в феврале его нет."""
    assert validate_due_day(5) == 5
    assert validate_due_day(29) == 1
    assert validate_due_day("abc") == 1


async def test_save_and_get_round_trip(demo, pool) -> None:
    """Карточка сохраняется и читается обратно с теми же данными."""
    from admin_core.billing_legal import get, save  # noqa: PLC0415

    salon_id = str(demo["salon"]["id"])
    result = await save(
        pool, salon_id, "llc", "ООО Ромашка", "5001007329",
        LLC_DETAILS, due_day=5, auto_invoice=True, contract_no="Д-42",
    )
    assert result["ok"] is True
    saved = await get(pool, salon_id)
    assert saved["client_type"] == "llc"
    assert saved["inn"] == "5001007329"
    assert saved["invoice_due_day"] == 5
    assert saved["auto_invoice"] is True
    assert saved["details"]["ogrn"] == "1027700132195"


async def test_save_rejects_invalid_without_writing(demo, pool) -> None:
    """Неверные реквизиты не попадают в базу: счёт по ним не выставить."""
    from admin_core.billing_legal import get, save  # noqa: PLC0415

    salon_id = str(demo["salon"]["id"])
    result = await save(pool, salon_id, "llc", "ООО Ромашка", "123", {})
    assert result["ok"] is False and result["errors"]
    assert await get(pool, salon_id) is None


class _Counter:
    """Заглушка метрик: worker-тесту не нужен Prometheus."""

    def labels(self, *_args: object) -> "_Counter":
        return self

    def inc(self) -> None:
        return None


async def test_auto_invoice_only_on_due_day(demo, pool) -> None:
    """Счёт выставляется только в дату из карточки плательщика.

    Карточку создаём прямо в БД, а не через save(): тест проверяет автосчёт,
    и лишняя запись в аудит только добавляет шагов до него.
    """
    import json  # noqa: PLC0415

    from admin_core.billing import auto_invoice_tick  # noqa: PLC0415

    salon_id = str(demo["salon"]["id"])
    await pool.execute(
        "UPDATE salons SET plan = $2, is_active = true WHERE id = $1",
        salon_id, "start",
    )
    await pool.execute(
        "INSERT INTO client_legal (salon_id, client_type, legal_name, inn, details, "
        "invoice_due_day, auto_invoice) "
        "VALUES ($1, 'ip', $2, $3, $4::jsonb, 1, true)",
        salon_id, "ИП Иванов", "123456789047", json.dumps(IP_DETAILS, ensure_ascii=False),
    )
    # Дата по часовому поясу контейнера: worker считает так же, иначе тест
    # ловил бы расхождение в один день на стыке суток.
    today = date.today().day

    async def mine(result: dict) -> list[str]:
        """Только счета нашего салона: тесты делят одну БД."""
        out: list[str] = []
        for payment_id in result["issued"]:
            owner = await pool.fetchval(
                "SELECT salon_id FROM payments WHERE id = $1", payment_id
            )
            if str(owner) == salon_id:
                out.append(payment_id)
        return out


    other_day = 2 if today != 2 else 3
    await pool.execute(
        "UPDATE client_legal SET invoice_due_day = $2 WHERE salon_id = $1",
        salon_id, other_day,
    )
    stored_day = await pool.fetchval(
        "SELECT invoice_due_day FROM client_legal WHERE salon_id = $1",
        salon_id,
    )
    assert int(stored_day) != today, (
        "тест должен ставить чужую дату, иначе проверка бессмысленна",
    )
    assert await mine(await auto_invoice_tick(pool)) == [], "в чужой день счёт не выставляется"

    await pool.execute(
        "UPDATE client_legal SET invoice_due_day = $2 WHERE salon_id = $1",
        salon_id, today,
    )
    result = await auto_invoice_tick(pool)
    assert result["errors"] == [], result
    assert len(await mine(result)) == 1, "в свой день счёт выставлен"
    assert await mine(await auto_invoice_tick(pool)) == [], "за период второй раз не выдаём"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
