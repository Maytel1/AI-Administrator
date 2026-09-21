"""CRM-адаптеры: internal (наш календарь, сценарий A) | altegio (по API)."""

from .base import CRMProvider, SlotTakenError


def make_crm(pool, provider: str = "internal", **kwargs):
    """Фабрика CRM-адаптера. Altegio включается при появлении доступа."""
    if provider == "internal":
        from .internal import InternalCRM

        return InternalCRM(pool)
    if provider == "altegio":
        from .altegio import AltegioCRM

        return AltegioCRM(pool, **kwargs)
    raise RuntimeError(f"Неизвестный crm_provider: {provider}")


__all__ = ["CRMProvider", "SlotTakenError", "make_crm"]
