"""Онлайн-оплата через эквайринг (ЮKassa и совместимые).

Ручной контур (перевод по реквизитам + подтверждение менеджером) остаётся
основным и по умолчанию: без ключей ``YOOKASSA_*`` провайдер не включается, и
оплата идёт как раньше. Этот модуль добавляет кнопку «Оплатить картой» рядом с
ручным СБП и приём уведомлений от провайдера.

Безопасность уведомлений. ЮKassa не подписывает вебхуки подписью тела, поэтому
доверять телу уведомления нельзя: им можно подделать ``payment.succeeded``. Правильный
контур — перечитать платёж у провайдера по id и убедиться, что он действительно
``status=succeeded``, ``paid=true`` и сумма совпадает с нашим счётом
(``verify_paid``). Ищем наш счёт по ``provider_payment_id``, который сами сохранили
при создании, — не по метаданным из уведомления. Дополнительно на уровне nginx
стоит ограничить приём уведомлений по IP провайдера (безопасность, фаза 3).

Логика сверки (``verify_paid``, ``parse_notification``, ``idempotence_key_for``)
намеренно чистые функции — покрыты автотестами без сети и без ключей.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Any

EVENT_SUCCEEDED = "payment.succeeded"
EVENT_CANCELED = "payment.canceled"


@dataclass
class YooKassaSettings:
    """Настройки магазина. secret — ключ API, в .env, не в коде и не в логах."""

    shop_id: str
    secret: str
    return_url: str = ""
    base_url: str = "https://api.yookassa.ru"


class NotConfigured(RuntimeError):
    """Ключи эквайринга не заданы — работаем в ручном режиме."""


def provider_from_env() -> YooKassaSettings | None:
    """Настройки из окружения либо ``None`` = эквайринг выключен.

    None, а не исключение: вызывающий код решает сам — показать кнопку оплаты
    картой или только ручной перевод. Отсутствие ключей не должно ломать вход.
    """
    shop = os.getenv("YOOKASSA_SHOP_ID", "")
    secret = os.getenv("YOOKASSA_SECRET_KEY", "")
    if not shop or not secret:
        return None
    return YooKassaSettings(
        shop_id=shop,
        secret=secret,
        return_url=os.getenv("YOOKASSA_RETURN_URL", ""),
        base_url=os.getenv("YOOKASSA_BASE_URL", "https://api.yookassa.ru"),
    )


def idempotence_key_for(*parts: str) -> str:
    """Стабильный ключ идемпотентности: один счёт — один ключ.

    Повторный запрос создания платежа (ретрайм сети, двойной клик) с тем же
    ключом вернёт уже созданный платёж, а не второй. Ключ детерминирован по
    составляющим счёта, поэтому воспроизводим без хранения.
    """
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def amount_value(amount: object) -> float | None:
    """Сумма платежа из ответа провайдера в рублях (float) или None.

    ЮKassa отдаёт сумму словарём ``{"value": "5000.00", "currency": "RUB"}``;
    принимаем и голое число на случай иного провайдера.
    """
    if isinstance(amount, dict):
        amount = amount.get("value")
    try:
        return float(str(amount))
    except (TypeError, ValueError):
        return None


def verify_paid(
    payment: dict, expected_amount_rub: float, *, allow_test: bool = False,
) -> tuple[bool, str]:
    """Годен ли платёж (перечитанный у провайдера) к зачислению.

    Возвращает ``(ok, причина_отказа)``. Проверяем три вещи, любую несостыковку
    из которых легко подделать в теле уведомления: статус ``succeeded`` и флаг
    ``paid``; сумма совпадает с нашим счётом; тестовый платёж не проходит в
    боевом режиме (иначе можно «оплатить» тестовым магазином).
    """
    if not isinstance(payment, dict):
        return False, "объект платежа не словарь"
    if payment.get("status") != "succeeded" or payment.get("paid") is not True:
        return False, f"статус {payment.get('status')!r}, paid={payment.get('paid')!r}"
    if payment.get("test") and not allow_test:
        return False, "тестовый платёж в боевом режиме"
    value = amount_value(payment.get("amount"))
    if value is None:
        return False, "нет суммы в объекте платежа"
    # допуск в пол-копейки — на случай строкового округления «5000.0» vs 5000
    if abs(value - float(expected_amount_rub)) > 0.005:
        return False, f"сумма {value} != ожидаемой {expected_amount_rub}"
    return True, ""


def parse_notification(payload: object) -> dict | None:
    """Разобрать уведомление ЮKassa → ``{event, payment_id}`` либо ``None``.

    None — если тело не похоже на уведомление (чужой POST, health-check). По
    этому id перечитываем платёж у провайдера — сам объект из уведомления не
    используем как источник истины.
    """
    if not isinstance(payload, dict):
        return None
    event = payload.get("event")
    obj = payload.get("object") or {}
    payment_id = obj.get("id") if isinstance(obj, dict) else None
    if not event or not payment_id:
        return None
    return {"event": str(event), "payment_id": str(payment_id)}


def verify_notification_secret(body: bytes, header: str, secret: str) -> bool:
    """Сравнение разделяемого секрета уведомления (constant-time).

    Не у всех провайдеров подпись есть; оставлено как defence-in-depth на случай
    кастомного прокси, добавляющего заголовок. Сравниваем в постоянном времени.
    """
    if not secret or not header:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)



class YooKassaProvider:
    """Клиент ЮKassa. Методы сетевые — в автотестах подменяются, не вызываются."""

    def __init__(self, settings: YooKassaSettings):
        self.s = settings

    def _client(self):
        # httpx импортируем лениво: чистую логику (verify_paid, parse_notification,
        # ключ идемпотентности) покрываем тестами без установленного httpx и без сети.
        import httpx

        # BasicAuth shopId:secret — так ЮKassa аутентифицирует магазин.
        return httpx.AsyncClient(
            timeout=30, auth=httpx.BasicAuth(self.s.shop_id, self.s.secret)
        )

    async def create_payment(
        self, *, amount_rub: float, description: str,
        idempotence_key: str, metadata: dict | None = None,
        return_url: str | None = None,
    ) -> dict:
        """Создать платёж. Возвращает ``{id, status, confirmation_url}``.

        ``capture=True`` — автосписание после подтверждения (для разовой оплаты
        подписки это то, что нужно; рекуррент — отдельная фаза).
        """
        confirmation = {"type": "redirect"}
        url = return_url or self.s.return_url
        if url:
            confirmation["return_url"] = url
        body = {
            "amount": {"value": f"{amount_rub:.2f}", "currency": "RUB"},
            "capture": True,
            "description": description[:128],
            "confirmation": confirmation,
            "metadata": metadata or {},
        }
        async with self._client() as client:
            resp = await client.post(
                f"{self.s.base_url}/v3/payments",
                json=body,
                headers={"Idempotence-Key": idempotence_key},
            )
            resp.raise_for_status()
            data = resp.json()
        return {
            "id": data.get("id"),
            "status": data.get("status"),
            "confirmation_url": (data.get("confirmation") or {}).get("confirmation_url"),
        }

    async def fetch_payment(self, payment_id: str) -> dict:
        """Перечитать платёж у провайдера — источник истины для зачисления."""
        async with self._client() as client:
            resp = await client.get(f"{self.s.base_url}/v3/payments/{payment_id}")
            resp.raise_for_status()
            return resp.json()


async def process_webhook_notification(
    pool: Any, provider: "YooKassaProvider", payload: object,
) -> str:
    """Обработать уведомление ЮKassa → одно из решений.

    Возвращает тег: ``paid`` (зачли), ``already`` (уже был оплачен — идемпотентно),
    ``ignored`` (не про нас / не успех), ``unknown`` (платежа нет в нашей БД),
    ``mismatch:<причина>`` (перечитанный платёж не сходится по сумме/статусу).

    Ключевая защита 152-ФЗ и денег: телу уведомления не доверяем. Находим наш счёт
    по ``provider_payment_id``, который сами сохранили при создании, перечитываем
    платёж у провайдера и сверяем сумму. Подделав ``payment.succeeded`` в теле,
    злоумышленник не создаст реальный успешный платёж на стороне ЮKassa.
    """
    notify = parse_notification(payload)
    if not notify or notify["event"] != EVENT_SUCCEEDED:
        return "ignored"
    payment_id = notify["payment_id"]
    row = await pool.fetchrow(
        "SELECT id, salon_id, amount, status FROM payments "
        "WHERE provider_payment_id = $1",
        payment_id,
    )
    if not row:
        return "unknown"
    if row["status"] == "paid":
        return "already"
    # Перечитываем у провайдера — тело уведомления не источник истины.
    provider_payment = await provider.fetch_payment(payment_id)
    ok, why = verify_paid(provider_payment, float(row["amount"]))
    if not ok:
        return f"mismatch:{why}"
    from . import billing  # noqa: PLC0415 — избегаем цикл импорта на уровне модуля

    result = await billing.mark_paid(
        pool, str(row["salon_id"]), str(row["id"]),
        actor="system:yookassa", note=f"webhook {payment_id}",
    )
    return "paid" if result.get("ok") else "already"


async def start_card_payment(
    pool: Any, provider: "YooKassaProvider", salon: dict, *, actor: str = "team:web",
) -> dict:
    """Выставить счёт и создать онлайн-платёж картой → вернуть ссылку на оплату.

    Порядок: наш счёт (``payments``) остаётся источником истины по сумме и периоду;
    ЮKassa только проводит платёж. Сначала ``issue_invoice`` (идемпотентно — один
    счёт на период), затем создаём платёж у провайдера со стабильным ключом
    идемпотентности и сохраняем ``provider_payment_id`` + ``confirmation_url`` в
    нашем счёте. Возврат: ``{ok, payment_id, confirmation_url, amount, invoice_no}``
    или ``{ok: False, error}``.

    Повторный вызов (двойной клик, ретрайм) не создаст ни второго счёта, ни второго
    платежа: счёт защищён ON CONFLICT, платёж — идемпотентным ключом.
    """
    from . import billing  # noqa: PLC0415 — избегаем цикл импорта на уровне модуля

    salon_id = str(salon["id"])
    invoice = await billing.issue_invoice(pool, salon, actor=actor)
    our_payment_id = invoice["payment_id"]
    amount = int(invoice["amount"])
    number = invoice["invoice_no"]
    ikey = idempotence_key_for(salon_id, number)

    created = await provider.create_payment(
        amount_rub=amount,
        description=f"Подписка AI-администратор, счёт {number}"[:128],
        idempotence_key=ikey,
        metadata={"payment_id": our_payment_id, "salon_id": salon_id},
        return_url=(provider.s.return_url or None),
    )
    provider_payment_id = created.get("id")
    confirmation_url = created.get("confirmation_url")
    if not provider_payment_id or not confirmation_url:
        return {"ok": False, "error": "провайдер не вернул ссылку на оплату"}

    await pool.execute(
        "UPDATE payments SET provider = 'yookassa', provider_payment_id = $2, "
        "confirmation_url = $3, idempotence_key = $4 WHERE id = $1",
        our_payment_id, provider_payment_id, confirmation_url, ikey,
    )
    return {
        "ok": True,
        "payment_id": our_payment_id,
        "provider_payment_id": provider_payment_id,
        "confirmation_url": confirmation_url,
        "amount": amount,
        "invoice_no": number,
    }
