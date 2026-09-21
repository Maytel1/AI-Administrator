"""Обезличивание ПДн перед отправкой в LLM (PDN_LLM_OPTIONS.md, вариант 1).

Имена/телефоны клиентов не должны попадать в зарубежные LLM (gpt-4o-mini,
DeepSeek). Для YandexGPT (данные в РФ) маскирование тоже включено по
умолчанию — дешёвая страховка. Сырой текст хранится только в нашей БД.
"""

import re

PHONE_RE = re.compile(r"(\+7|8)[\s\-()]*(\d{3})[\s\-()]*(\d{3})[\s\-()]*(\d{2})[\s\-()]*(\d{2})")


def mask_phone(text: str) -> str:
    return PHONE_RE.sub("[ТЕЛЕФОН]", text)


def mask_name(text: str, name: str | None) -> str:
    if not name or len(name) < 2:
        return text
    return re.sub(re.escape(name), "Клиент", text, flags=re.IGNORECASE)


def anonymize_for_llm(text: str, client_name: str | None = None) -> str:
    out = mask_phone(text)
    return mask_name(out, client_name)
