"""Контракт CRM-адаптера (DEVELOPMENT_PLAN.md, фаза 1).

Реализации: internal.py (наш календарь — сценарий A для салонов без CRM),
altegio.py (заготовка по API-контракту, UNVERIFIED без доступа).
"""

from abc import ABC, abstractmethod
from typing import Any


class SlotTakenError(Exception):
    """Слот занят (asyncpg 23P01 от exclusion-констрейнта) — агент предлагает другое время."""


class CRMProvider(ABC):
    @abstractmethod
    async def get_client_by_phone(self, salon_id: str, phone: str) -> dict | None: ...

    @abstractmethod
    async def ensure_client(
        self, salon_id: str, phone: str, name: str | None = None
    ) -> dict: ...

    @abstractmethod
    async def get_services(self, salon_id: str) -> list[dict]: ...

    @abstractmethod
    async def get_masters(self, salon_id: str) -> list[dict]: ...

    @abstractmethod
    async def get_free_slots(
        self, salon_id: str, service_title: str, days_ahead: int = 2
    ) -> list[dict]: ...

    @abstractmethod
    async def create_booking(
        self,
        salon_id: str,
        client_id: str,
        service_title: str,
        time_text: str,
        offered: list[dict] | None = None,
        created_by: str = "bot",
    ) -> dict: ...

    @abstractmethod
    async def cancel_my_last_booking(self, salon_id: str, client_id: str) -> dict: ...

    @abstractmethod
    async def get_client_profile(self, salon_id: str, client_id: str) -> dict: ...


def audit(actor: str, salon_id: str | None, action: str, details: dict[str, Any]) -> dict:
    """Единый формат записи в audit_log (LIABILITY_OPTIONS.md)."""
    return {"actor": actor, "salon_id": salon_id, "action": action, "details": details}
