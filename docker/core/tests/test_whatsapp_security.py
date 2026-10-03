"""Подпись webhook Meta: единственная защита эндпоинта WhatsApp.

Если проверка подписи ослаблена, любой желающий сможет писать в салон от имени
клиентов — поэтому здесь и подпись, и её отсутствие, и чужое тело.
"""

import pytest

from admin_core.channels.whatsapp_auth import sign_body, verify_signature

SECRET = "app-secret-тест"


def test_valid_signature_passes() -> None:
    body = b'{"entry":[]}'
    assert verify_signature(body, sign_body(body, SECRET), SECRET) is True


def test_tampered_body_fails() -> None:
    body = b'{"entry":[]}'
    signature = sign_body(body, SECRET)
    assert verify_signature(body + b' ', signature, SECRET) is False
    assert verify_signature(b'{"entry":[{"x":1}]}', signature, SECRET) is False


def test_wrong_secret_fails() -> None:
    body = b"{}"
    signature = sign_body(body, SECRET)
    assert verify_signature(body, signature, "чужой-секрет") is False


def test_missing_or_empty_inputs_fail_closed() -> None:
    """Нет секрета, нет заголовка, пустое тело — всё отклоняется, не пропускается."""
    body = b"{}"
    assert verify_signature(body, "", SECRET) is False
    assert verify_signature(body, sign_body(body, SECRET), "") is False
    assert verify_signature(body, "sha1=abc", SECRET) is False, "только sha256"
    assert verify_signature(b"", "sha256=abc", SECRET) is False


def test_signature_is_deterministic() -> None:
    body = b'{"a":1}'
    assert sign_body(body, SECRET) == sign_body(body, SECRET)
    assert sign_body(body, SECRET) != sign_body(b'{"a":2}', SECRET)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
