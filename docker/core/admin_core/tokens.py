"""Сравнение токенов константным временем.

Вынесено отдельно, потому что правило нужное и легко нарушаемое: сравнивать
токен как обычные строки нельзя (время ответа зависит от первого различия),
а через ``hmac.compare_digest`` — можно, но только по байтам: на строке с
не-ASCII символами он бросает TypeError, и запрос с «русским» заголовком
превращается в 500.
"""

from __future__ import annotations

import hmac


def matches(supplied: str | None, expected: str | None) -> bool:
    """Верно ли предъявленный токен. Пустой ожидаемый — сервис закрыт."""
    if not expected:
        return False
    return hmac.compare_digest(
        (supplied or "").encode("utf-8"), expected.encode("utf-8")
    )


def strip_bearer(header: str | None) -> str:
    """Значение заголовка Authorization без префикса «Bearer »."""
    value = (header or "").strip()
    return value[7:] if value.startswith("Bearer ") else value
