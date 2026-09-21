"""Обезличивание ПДн перед LLM (PDN_LLM_OPTIONS.md, вариант 1)."""

from admin_core.anonymize import anonymize_for_llm, mask_phone


def test_phone_masked():
    text = "Запишите меня, телефон +7 (900) 123-45-67, завтра в 11:00"
    masked = mask_phone(text)
    assert "+7" not in masked and "123-45-67" not in masked
    assert "[ТЕЛЕФОН]" in masked
    assert "11:00" in masked  # время не трогаем


def test_phone_plain_digits():
    assert mask_phone("89001234567") == "[ТЕЛЕФОН]"


def test_name_masked_case_insensitive():
    text = "Иван просил записать Ивана к Анне"
    out = anonymize_for_llm(text, client_name="Иван")
    assert "Иван" not in out.replace("иванов", "")
    assert "Клиент просил записать Клиента к Анне" == out


def test_no_name_no_change():
    text = "хочу записаться на стрижку"
    assert anonymize_for_llm(text) == text
