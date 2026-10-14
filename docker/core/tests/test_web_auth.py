"""Тесты доступа в панель: пароли (PBKDF2), TOTP (RFC 6238), роли и вход.

Векторы TOTP берём из самого RFC 6238 (секрет «12345678901234567890»),
иначе тест проверит только нашу же реализацию.
"""

from admin_core.web.auth import (
    TOTP_DIGITS,
    generate_totp_secret,
    hash_password,
    provisioning_uri,
    totp_code,
    verify_password,
    verify_totp,
)
from admin_core.web.roles import (
    ROLE_PERMISSIONS,
    audit_actor,
    can,
    can_action,
    permissions_of,
    role_label,
)

# ═══ ПАРОЛИ ═════════════════════════════════════════════════════════════


def test_password_hash_is_verifiable_and_salted():
    stored = hash_password("очень-длинный-пароль")
    assert stored.startswith("pbkdf2_sha256$")
    assert verify_password("очень-длинный-пароль", stored) is True
    assert verify_password("другой-пароль", stored) is False
    # две соли → разные хеши для одного пароля
    assert hash_password("пароль-длинный") != hash_password("пароль-длинный")


def test_password_hash_rejects_short_password():
    try:
        hash_password("коротк")
    except ValueError as exc:
        assert "8" in str(exc)
    else:
        raise AssertionError("короткий пароль должен отклоняться")


def test_verify_password_survives_broken_stored():
    """Мусор в БД не должен ронять вход — это «неверный пароль», а не 500."""
    for broken in ("", "мусор", "pbkdf2_sha256$x$y", "pbkdf2_md5$1$a$b"):
        assert verify_password("любой", broken) is False


def test_plain_password_never_stored():
    stored = hash_password("секретный-пароль")
    assert "секретный-пароль" not in stored


# ═══ TOTP (RFC 6238) ═════════════════════════════════════════════════════

RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890" в base32
RFC_VECTORS = [
    (59, "287082"),
    (1111111109, "081804"),
    (1111111111, "050471"),
    (1234567890, "005924"),
    (2000000000, "279037"),
]


def test_totp_matches_rfc6238_vectors():
    for timestamp, expected in RFC_VECTORS:
        assert totp_code(RFC_SECRET, at=timestamp) == expected


def test_totp_code_shape():
    code = totp_code(generate_totp_secret())
    assert len(code) == TOTP_DIGITS and code.isdigit()


def test_verify_totp_window():
    secret = generate_totp_secret()
    now = 1_700_000_000
    assert verify_totp(secret, totp_code(secret, at=now), at=now) is True
    # ±1 шаг (30 сек) — часы телефона спешат
    assert verify_totp(secret, totp_code(secret, at=now - 30), at=now) is True
    assert verify_totp(secret, totp_code(secret, at=now + 30), at=now) is True
    # ±2 шага — уже нет
    assert verify_totp(secret, totp_code(secret, at=now - 60), at=now) is False


def test_verify_totp_rejects_garbage():
    secret = generate_totp_secret()
    now = 1_700_000_000
    for bad in ("", "12345", "abcdef", "1234567", None, "00000a"):
        assert verify_totp(secret, bad, at=now) is False
    assert verify_totp("", "123456", at=now) is False  # 2FA выключена — пустой секрет


def test_provisioning_uri_shape():
    uri = provisioning_uri("ABCDEFGH", "owner@example.com")
    assert uri.startswith("otpauth://totp/")
    assert "secret=ABCDEFGH" in uri
    assert "issuer=Dezhurny24" in uri
    assert "digits=6" in uri and "period=30" in uri


# ═══ РОЛИ И ПРАВА ════════════════════════════════════════════════════════


def test_role_matrix_is_explicit():
    assert set(ROLE_PERMISSIONS) == {"owner", "staff", "team"}
    # администратор точки не правит цены и расписание
    assert can("staff", "read") is True
    assert can("staff", "booking_status") is True
    assert can("staff", "pricing") is False
    assert can("staff", "schedule") is False
    # владелец правит всё в своей точке, но не видит чужие точки
    assert can("owner", "pricing") is True
    assert can("owner", "users") is True
    assert can("owner", "all_salons") is False
    # наша команда видит всё
    assert can("team", "all_salons") is True


def test_unknown_role_has_no_permissions():
    assert can("root", "pricing") is False
    assert can("guest", "read") is False
    assert can_action("guest", "service_save") is False


def test_action_permission_mapping():
    assert can_action("owner", "service_save") is True
    assert can_action("staff", "service_save") is False
    assert can_action("staff", "booking_status") is True
    assert can_action("staff", "hours_save") is False
    assert can_action("staff", "hours_save_week") is False
    assert can_action("staff", "master_services_save") is False
    assert can_action("staff", "client_save") is True
    assert can_action("staff", "unknown_action") is False
    # свой пароль меняет любая роль, сброс чужого — только owner/team
    assert can_action("staff", "password_change") is True
    assert can_action("staff", "password_reset") is False
    assert can_action("owner", "password_reset") is True
    assert can_action("team", "password_reset") is True


def test_labels_and_actor():
    assert role_label("owner") == "Владелец точки"
    assert "цены и услуги" in permissions_of("owner")
    assert audit_actor("owner", "boss@example.com") == "owner:boss@example.com"
    assert audit_actor("staff", "recep@example.com") == "staff:recep@example.com"
    assert audit_actor("team", "admin@example.com").startswith("admin:")


if __name__ == "__main__":  # pragma: no cover
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
