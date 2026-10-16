"""Реестр контрагентов: общий справочник подключённых клиентов.

Его данные питают документы (счёт/акт) и списки выбора, поэтому логика CRUD,
валидация по типу и фильтрация активных должны работать без сюрпризов. Архив —
не удаление: ранее выставленные документы остаются валидными.
"""

from admin_core import counterparties
from admin_core.billing_legal import validate_inn

IP_INN = "5001007329"  # 10 цифр, валидный (юрлицо/ООО)
IP12_INN = "123456789047"  # 12 цифр, валидный (ИП/физлицо)
IP_OGRNIP = "304774600000013"


async def _clean(pool):
    """Убрать за собой: реестр общий, чужие строки мешают счётчикам."""
    await pool.execute(
        "DELETE FROM counterparties WHERE title IN "
        "('ООО Тест', 'ИП До', 'ИП После', 'ИП Архив')"
    )


async def test_save_creates_and_reads_back(pool):
    """Создание + чтение: данные доходят до БД и парсятся из jsonb."""
    try:
        result = await counterparties.save(
            pool, title="ООО Тест", client_type="llc",
            legal_name="ООО Тест", inn=IP_INN,
            details={"kpp": "667001001", "ogrn": "1027700132195",
                     "address": "620000, Екатеринбург", "director": "Иванов И.И.",
                     "bank": "Сбер", "bik": "044525225",
                     "account": "40702810900000000001"},
            contract_no="Д-1",
        )
        assert result["ok"], result
        row = await counterparties.get(pool, result["id"])
        assert row is not None
        assert row["title"] == "ООО Тест"
        assert row["inn"] == IP_INN
        assert row["details"]["ogrn"] == "1027700132195"
        assert row["contract_no"] == "Д-1"
    finally:
        await _clean(pool)


async def test_save_rejects_bad_inn_and_missing_title(pool):
    """Все ошибки разом: неверный ИНН и пустое название."""
    result_bad_inn = await counterparties.save(
        pool, title="X", client_type="llc", legal_name="X",
        inn="123", details={},
    )
    assert not result_bad_inn["ok"]
    assert any("ИНН" in e for e in result_bad_inn["errors"])

    result_no_title = await counterparties.save(
        pool, title="   ", client_type="ip", legal_name="Y",
        inn=IP12_INN, details={"ogrnip": IP_OGRNIP, "address": "620000"},
    )
    assert not result_no_title["ok"]
    assert any("Название" in e for e in result_no_title["errors"])


async def test_update_changes_fields(pool):
    """Правка: обновляются и основные поля, и jsonb-реквизиты."""
    try:
        created = await counterparties.save(
            pool, title="ИП До", client_type="ip", legal_name="ИП До",
            inn=IP12_INN, details={"ogrnip": IP_OGRNIP, "address": "620000"},
        )
        cid = created["id"]
        updated = await counterparties.save(
            pool, counterparty_id=cid, title="ИП После", client_type="ip",
            legal_name="ИП После", inn=IP12_INN,
            details={"ogrnip": IP_OGRNIP, "address": "625000, Новая"},
        )
        assert updated["ok"]
        row = await counterparties.get(pool, cid)
        assert row["title"] == "ИП После"
        assert row["details"]["address"] == "625000, Новая"
    finally:
        await _clean(pool)


async def test_archive_hides_from_active_options(pool):
    """Архив убирает из списков выбора, но не удаляет запись."""
    try:
        created = await counterparties.save(
            pool, title="ИП Архив", client_type="ip", legal_name="ИП Архив",
            inn=IP12_INN, details={"ogrnip": IP_OGRNIP, "address": "620000"},
        )
        cid = created["id"]
        active_ids = {str(o["id"]) for o in await counterparties.active_options(pool)}
        assert cid in active_ids

        await counterparties.set_active(pool, cid, False)
        active_ids = {str(o["id"]) for o in await counterparties.active_options(pool)}
        assert cid not in active_ids
        # Запись не удалена — просто неактивна.
        assert await counterparties.get(pool, cid) is not None

        await counterparties.set_active(pool, cid, True)
        active_ids = {str(o["id"]) for o in await counterparties.active_options(pool)}
        assert cid in active_ids
    finally:
        await _clean(pool)


async def test_toggle_unknown_id_reports_not_found(pool):
    result = await counterparties.set_active(pool, "00000000-0000-0000-0000-000000000000", False)
    assert result["ok"] is False
    assert result["error"] == "not_found"


def test_validate_reuses_legal_checks():
    """Та же контрольная сумма ИНН, что и у карточки плательщика."""
    assert validate_inn(IP_INN) is True
    assert validate_inn("5001007328") is False
