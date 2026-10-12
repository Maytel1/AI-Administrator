"""Эквайринг: проверки решений без сети и без ключей.

Тестируем ровно то, что легко подделать и что решает судьбу платежа: сверку
перечитанного у провайдера платежа, разбор уведомления и детерминизм ключа
идемпотентности. Сети здесь нет и быть не должно: вызов в живой ЮKassa в тесте
зависит от ключей и платит реальными деньгами при промахе.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_SCRIPT = Path(__file__).resolve().parents[1] / "admin_core" / "acquiring.py"
_spec = importlib.util.spec_from_file_location("acquiring", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod: ModuleType = importlib.util.module_from_spec(_spec)
# Регистрируем до exec_module: @dataclass в новых Python резолвит аннотации
# (KW_ONLY) через sys.modules[__module__], и без этой строки падает сборка.
sys.modules["acquiring"] = mod
_spec.loader.exec_module(mod)


def _paid_payment(**over):
    """Корректный успешный платёж, как его отдаёт ЮKassa."""
    pay = {"status": "succeeded", "paid": True, "amount": {"value": "5000.00", "currency": "RUB"}}
    pay.update(over)
    return pay


def test_provider_disabled_without_keys(monkeypatch) -> None:
    """Без ключей провайдер выключен (None), а не исключение — вход не ломается."""
    monkeypatch.delenv("YOOKASSA_SHOP_ID", raising=False)
    monkeypatch.delenv("YOOKASSA_SECRET_KEY", raising=False)
    assert mod.provider_from_env() is None


def test_provider_enabled_with_keys(monkeypatch) -> None:
    monkeypatch.setenv("YOOKASSA_SHOP_ID", "123")
    monkeypatch.setenv("YOOKASSA_SECRET_KEY", "s3cr3t")
    settings = mod.provider_from_env()
    assert settings is not None
    assert settings.shop_id == "123"


def test_verify_paid_accepts_real_payment() -> None:
    ok, why = mod.verify_paid(_paid_payment(), 5000)
    assert ok, why


def test_verify_paid_rejects_pending() -> None:
    """Незавершённый платёж не зачисляем — иначе салон «оплатит» и отменит."""
    ok, why = mod.verify_paid(_paid_payment(status="pending", paid=False), 5000)
    assert not ok
    assert "pending" in why


def test_verify_paid_rejects_wrong_amount() -> None:
    """Частая атака вебхука: succeeded, но на 1 рубль вместо 5000."""
    ok, why = mod.verify_paid(_paid_payment(amount={"value": "1.00", "currency": "RUB"}), 5000)
    assert not ok
    assert "сумма" in why


def test_verify_paid_rejects_test_in_prod() -> None:
    """Тестовый платёж не должен зачисляться в боевом режиме."""
    ok, why = mod.verify_paid(_paid_payment(test=True), 5000)
    assert not ok
    assert "тестовый" in why
    # ...но в отладочном режиме — годится (проверяем весь контур на копейках).
    assert mod.verify_paid(_paid_payment(test=True), 5000, allow_test=True)[0]


def test_verify_paid_rejects_missing_amount() -> None:
    assert not mod.verify_paid(_paid_payment(amount=None), 5000)[0]


def test_verify_paid_rejects_non_dict() -> None:
    assert not mod.verify_paid("not a payment", 5000)[0]


def test_amount_value_reads_dict_and_number() -> None:
    assert mod.amount_value({"value": "5000.00"}) == 5000.0
    assert mod.amount_value(12) == 12.0
    assert mod.amount_value(None) is None
    assert mod.amount_value("нет") is None


def test_parse_notification_extracts_ids() -> None:
    parsed = mod.parse_notification({"event": "payment.succeeded", "object": {"id": "2c5f"}})
    assert parsed == {"event": "payment.succeeded", "payment_id": "2c5f"}


def test_parse_notification_rejects_junk() -> None:
    assert mod.parse_notification({"type": "notification"}) is None  # нет event/object
    assert mod.parse_notification("garbage") is None
    assert mod.parse_notification({"event": "payment.succeeded", "object": {}}) is None


def test_idempotence_key_is_stable_and_distinct() -> None:
    """Один счёт — один ключ (ретрайм не создаст второй платёж)."""
    assert mod.idempotence_key_for("salon1", "2026-10-01") == mod.idempotence_key_for("salon1", "2026-10-01")
    assert mod.idempotence_key_for("salon1", "2026-10-01") != mod.idempotence_key_for("salon2", "2026-10-01")


def test_verify_notification_secret_constant_time() -> None:
    import hashlib
    import hmac

    secret = "proxy-secret"
    good = hmac.new(secret.encode(), b"body", hashlib.sha256).hexdigest()
    assert mod.verify_notification_secret(b"body", good, secret)
    assert not mod.verify_notification_secret(b"body", "0" * 64, secret)
    assert not mod.verify_notification_secret(b"body", "", secret)  # пустой секрет/заголовок
