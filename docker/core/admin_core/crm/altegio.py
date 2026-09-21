"""AltegioCRM — заготовка по API-контракту Altegio (DIKIDI).

⚠️ UNVERIFIED: пишется по открытой документации Altegio Partner API v1
(https://altegio.com/api), без живого ключа. После получения доступа
(бесплатный тариф + партнёрский токен) — прогнать контрактные тесты и
поправить пути/поля. YCLIENTS подключается по этому же интерфейсу позже.

Ключи: ALTEGIO_PARTNER_TOKEN, ALTEGIO_COMPANY_ID (в docker/.env; секреты
позже — в Lockbox, DEVELOPMENT_PLAN.md фаза 2–3).
"""

import httpx

from .base import CRMProvider, SlotTakenError


class AltegioNotConfigured(RuntimeError):
    pass


class AltegioCRM(CRMProvider):
    def __init__(self, pool, partner_token: str | None, company_id: str | None,
                 base_url: str = "https://api.altegio.ru/api/v1"):
        if not partner_token or not company_id:
            raise AltegioNotConfigured(
                "ALTEGIO_PARTNER_TOKEN / ALTEGIO_COMPANY_ID не заданы в .env"
            )
        self.pool = pool
        self.company_id = company_id
        self.base = base_url.rstrip("/")
        self.headers = {"Partner-Token": partner_token, "Accept": "application/json",
                        "Content-Type": "application/json"}

    async def _call(self, method: str, path: str, json_body: dict | None = None,
                    params: dict | None = None) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(
                method, f"{self.base}{path}", headers=self.headers,
                json=json_body, params=params,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_services(self, salon_id: str) -> list[dict]:
        # UNVERIFIED: /book_services/{company_id} → data[].{id, title, price_min, duration}
        data = (await self._call("GET", f"/book_services/{self.company_id}"))["data"]
        return [
            {
                "external_id": str(s["id"]),
                "title": s["title"],
                "duration_min": int(s.get("duration", 60)),
                "price": float(s.get("price_min") or 0),
            }
            for s in data
        ]

    async def get_masters(self, salon_id: str) -> list[dict]:
        # UNVERIFIED: /book_staff/{company_id} → data[].{id, name}
        data = (await self._call("GET", f"/book_staff/{self.company_id}"))["data"]
        return [{"external_id": str(s["id"]), "name": s["name"]} for s in data]

    async def get_free_slots(self, salon_id: str, service_title: str, days_ahead: int = 2):
        # UNVERIFIED: /book_dates → /book_times?staff_id=&service_id=&date=YYYY-MM-DD
        raise NotImplementedError("фаза 1: реализовать после получения доступа к Altegio")

    async def create_booking(self, salon_id, client_id, service_title, time_text,
                             offered=None, created_by="bot"):
        # UNVERIFIED: POST /book_record/{company_id}
        # {"staff_id":..., "datetime":"YYYY-MM-DDTHH:mm:ss", "services":[{"id":..., "count":1}]}
        raise NotImplementedError("фаза 1: реализовать после получения доступа к Altegio")

    async def cancel_my_last_booking(self, salon_id: str, client_id: str) -> dict:
        # UNVERIFIED: DELETE /book_record/{company_id}/{record_id}
        raise NotImplementedError("фаза 1: реализовать после получения доступа к Altegio")

    async def get_client_by_phone(self, salon_id: str, phone: str) -> dict | None:
        # UNVERIFIED: /clients/{company_id}?phone=... (требует user-токен салона)
        raise NotImplementedError("фаза 1: реализовать после получения доступа к Altegio")

    async def ensure_client(self, salon_id: str, phone: str, name: str | None = None) -> dict:
        raise NotImplementedError("фаза 1: реализовать после получения доступа к Altegio")

    async def get_client_profile(self, salon_id: str, client_id: str) -> dict:
        raise NotImplementedError("фаза 1: реализовать после получения доступа к Altegio")


def slot_taken(httpx_response_status: int) -> bool:
    """Хелпер маппинга ошибок Altegio на SlotTakenError (уточнить коды по докам)."""
    return httpx_response_status in (409, 422)
