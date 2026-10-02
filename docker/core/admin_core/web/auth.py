"""Пароли (PBKDF2) и TOTP (RFC 6238) для входа в панель.

Только стандартная библиотека: в контуре нет pip, а тянуть bcrypt/pyotp ради
двух функций незачем. PBKDF2-HMAC-SHA256 с солью на пользователя — приемлемо
для панели точки; для админки команды можно перейти на argon2 (см. §8.2 плана).

Секреты TOTP и хеши паролей сравниваются константным временем.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
from urllib.parse import quote

PBKDF2_ROUNDS = 240_000
TOTP_STEP = 30  # секунд — как в Google Authenticator по умолчанию
TOTP_DIGITS = 6
TOTP_WINDOW = 1  # допуск ±1 шаг: часы клиента спешат/отстают
_ISSUER = "Dezhurny24"


# ── пароли ────────────────────────────────────────────────────────────────


def hash_password(password: str, *, rounds: int = PBKDF2_ROUNDS) -> str:
    """Возвращает 'pbkdf2_sha256$<rounds>$<salt_hex>$<hash_hex>' для хранения в БД."""
    if len(password) < 8:
        raise ValueError("пароль короче 8 символов")
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    return f"pbkdf2_sha256${rounds}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Сверяем пароль с хешем; мусор в stored считаем неудачей, а не исключением."""
    try:
        algo, rounds, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        expected = bytes.fromhex(hash_hex)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds)
        )
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(expected, actual)


# ── TOTP (RFC 4226 / 6238) ────────────────────────────────────────────────


def generate_totp_secret(nbytes: int = 20) -> str:
    """Новый base32-секрет (160 бит — длина по умолчанию в RFC 4226)."""
    return base64.b32encode(os.urandom(nbytes)).decode("ascii").rstrip("=")


def _base32_decode(secret: str) -> bytes:
    padded = secret.upper().replace(" ", "")
    padded += "=" * (-len(padded) % 8)
    return base64.b32decode(padded, casefold=True)


def totp_code(secret: str, at: float | None = None) -> str:
    """Шестизначный код TOTP для момента времени ``at`` (по умолчанию — сейчас)."""
    counter = int((time.time() if at is None else at) // TOTP_STEP)
    mac = hmac.new(_base32_decode(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFF_FFFF
    return str(code % (10**TOTP_DIGITS)).zfill(TOTP_DIGITS)


def verify_totp(secret: str, code: str, *, at: float | None = None) -> bool:
    """Проверка кода с допуском ±1 окно (часы телефона могут спешить на минуту)."""
    code = (code or "").strip()
    if not secret or not code.isdigit() or len(code) != TOTP_DIGITS:
        return False
    moment = time.time() if at is None else at
    return any(
        hmac.compare_digest(totp_code(secret, moment + shift * TOTP_STEP), code)
        for shift in range(-TOTP_WINDOW, TOTP_WINDOW + 1)
    )


def provisioning_uri(secret: str, login: str, issuer: str = _ISSUER) -> str:
    """otpauth://-ссылка для «Google Authenticator» и подобных приложений."""
    label = quote(f"{issuer}:{login}")
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
        f"&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_STEP}"
    )


def random_password(length: int = 16) -> str:
    """Пароль для первого входа, если владелец не задал свой."""
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))
