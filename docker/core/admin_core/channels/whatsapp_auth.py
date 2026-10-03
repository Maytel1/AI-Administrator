"""Проверка подписи webhook Meta (X-Hub-Signature-256).

Зачем отдельным модулем: подпись — единственное, что отличает настоящий вызов
Meta от чужого POST в наш эндпоинт. Ошибка здесь означает, что любой желающий
сможет писать в салон от имени клиентов.
"""

from __future__ import annotations

import hashlib
import hmac

HEADER = "X-Hub-Signature-256"
PREFIX = "sha256="


def verify_signature(body: bytes, header: str, secret: str) -> bool:
    """Проверить подпись тела запроса.

    Сравнение постоянного времени: иначе по времени ответа можно подобрать
    подпись побайтово.
    """
    if not secret or not header or not header.startswith(PREFIX):
        return False
    expected = PREFIX + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header)


def sign_body(body: bytes, secret: str) -> str:
    """Подпись для тестов и для локальной проверки скриптом."""
    return PREFIX + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
