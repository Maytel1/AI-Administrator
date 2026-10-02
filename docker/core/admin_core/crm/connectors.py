"""Готовность внешних CRM-коннекторов.

Altegio и YCLIENTS не подключены: для них нужны ключи и доступ к API, которых
у нас нет. Молчащий «адаптер-заглушка» опаснее отсутствия адаптера — он
выглядит работающим, пока не сорвёт запись у клиента.

Поэтому здесь только проверка готовности: что настроено, чего не хватает и
что именно сделать, чтобы включить. Плюс тревога, если у точки выбран
коннектор без ключей: такая точка сейчас не записывает клиентов.
"""

from __future__ import annotations

import os
from typing import Any

# Коннектор → какие переменные нужны для чтения (услуги, мастера, слоты).
CONNECTORS: dict[str, dict[str, Any]] = {
    "internal": {
        "label": "Наш календарь",
        "env": [],
        "note": "Работает всегда: данные в этой же базе.",
    },
    "altegio": {
        "label": "Altegio (DIKIDI)",
        "env": ["ALTEGIO_TOKEN", "ALTEGIO_BRANCH_ID"],
        "note": "Только чтение услуг, мастеров и слотов; записи остаются у нас.",
    },
    "yclients": {
        "label": "YCLIENTS",
        "env": ["YCLIENTS_TOKEN", "YCLIENTS_COMPANY_ID"],
        "note": "Только чтение; запись и перенос — после проверки на пилоте.",
    },
}


def connector_status(name: str, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Что нужно коннектору и чего не хватает прямо сейчас."""
    source = env if env is not None else dict(os.environ)
    spec = CONNECTORS.get(name)
    if not spec:
        return {
            "provider": name, "known": False, "ready": False,
            "missing": [], "label": "неизвестный коннектор",
            "note": "Такого crm_provider в списке нет — точка не сможет работать.",
        }
    missing = [key for key in spec["env"] if not source.get(key)]
    return {
        "provider": name,
        "known": True,
        "ready": name == "internal" or not missing,
        "label": spec["label"],
        "missing": missing,
        "note": spec["note"],
    }


def salon_connector_report(salon: dict[str, Any], env: dict[str, str] | None = None
                           ) -> dict[str, Any]:
    """Состояние коннектора конкретной точки — для панели и тревог."""
    status = connector_status(str(salon.get("crm_provider") or "internal"), env)
    status["salon_id"] = str(salon.get("id", ""))
    status["salon"] = salon.get("name", "")
    return status


def unavailable_salons(salons: list[dict[str, Any]], env: dict[str, str] | None = None
                       ) -> list[dict[str, Any]]:
    """Точки, которые просят внешний коннектор, а ключей для него нет."""
    return [
        salon_connector_report(salon, env)
        for salon in salons
        if str(salon.get("crm_provider") or "internal") != "internal"
        and not connector_status(
            str(salon.get("crm_provider")), env
        )["ready"]
    ]


def summary(env: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Состояние всех известных коннекторов — для /health и диагностики."""
    return [connector_status(name, env) for name in CONNECTORS]
