"""Инструменты агента (function calling) поверх CRM-адаптера.

Названия и схемы — в формате OpenAI tools; провайдер yandexgpt/fake работают
с тем же dispatch. Ошибка slot_taken (23P01) возвращается агенту как
результат инструмента, а не исключение — модель предлагает другое время.
"""

import json

from ..crm.base import CRMProvider, SlotTakenError

TOOL_SPECS = [
    {
        "name": "list_services",
        "description": "Список услуг салона с ценами и длительностью.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_free_slots",
        "description": "Свободные слоты для услуги на ближайшие 2 дня.",
        "parameters": {
            "type": "object",
            "properties": {"service": {"type": "string", "description": "название услуги"}},
            "required": ["service"],
        },
    },
    {
        "name": "create_booking",
        "description": "Создать запись. Время — только из ранее предложенных слотов.",
        "parameters": {
            "type": "object",
            "properties": {
                "service": {"type": "string"},
                "time": {"type": "string", "description": "HH:MM из предложенного слота"},
            },
            "required": ["service", "time"],
        },
    },
    {
        "name": "cancel_my_last_booking",
        "description": "Отменить последнюю активную запись клиента.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_client_profile",
        "description": "Профиль клиента: имя, заметки, история визитов, будущая запись.",
        "parameters": {"type": "object", "properties": {}},
    },
]


def build_dispatch(crm: CRMProvider, salon_id: str, client_id: str, created_by: str = "bot"):
    """Возвращает async dispatch(name, args) -> dict (JSON-безопасный)."""

    async def dispatch(name: str, args: dict) -> dict:
        try:
            if name == "list_services":
                services = await crm.get_services(salon_id)
                return {
                    "services": [
                        {
                            "title": s["title"],
                            "duration_min": s["duration_min"],
                            "price": float(s["price"]) if s.get("price") else None,
                        }
                        for s in services
                    ]
                }
            if name == "get_free_slots":
                service = str(args.get("service", "")).strip()
                if not service:
                    # модель иногда зовёт инструмент без параметра: вместо пустого
                    # ответа отдаём список услуг, чтобы она переспросила клиента
                    titles = [s["title"] for s in await crm.get_services(salon_id)]
                    return {
                        "error": "service_required",
                        "question": "На какую услугу показать слоты?",
                        "services": titles,
                    }
                return {"slots": await crm.get_free_slots(salon_id, service)}
            if name == "create_booking":
                return await crm.create_booking(
                    salon_id, client_id, args.get("service", ""),
                    args.get("time", ""), created_by=created_by,
                )
            if name == "cancel_my_last_booking":
                return await crm.cancel_my_last_booking(salon_id, client_id)
            if name == "get_client_profile":
                return {"profile": await crm.get_client_profile(salon_id, client_id)}
            return {"ok": False, "error": "unknown_tool", "tool": name}
        except SlotTakenError:
            return {"ok": False, "error": "slot_taken", "attempted": args.get("time", "")}
        except Exception as exc:  # noqa: BLE001 — агенту важен факт сбоя, не трейс
            return {"ok": False, "error": "internal_error", "detail": type(exc).__name__}

    return dispatch


def specs_json() -> str:
    return json.dumps(TOOL_SPECS, ensure_ascii=False)
