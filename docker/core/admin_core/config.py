"""Конфигурация из окружения (.env подхватывается docker compose)."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL", "postgresql://aiadmin:aiadmin_dev@localhost:5433/aiadmin"
    )
    # fake — работает без ключей (демо/тесты); yandexgpt — после создания YC-аккаунта
    llm_provider: str = os.getenv("LLM_PROVIDER", "fake")
    yc_api_key: str | None = os.getenv("YC_API_KEY")
    yc_folder_id: str | None = os.getenv("YC_FOLDER_ID")
    yandexgpt_model_uri: str | None = os.getenv(
        "YANDEXGPT_MODEL_URI"  # например gpt://b1g.../yandexgpt-lite
    )
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    altegio_base_url: str = os.getenv("ALTEGIO_BASE_URL", "https://api.altegio.ru/api/v1")
    altegio_partner_token: str | None = os.getenv("ALTEGIO_PARTNER_TOKEN")
    altegio_company_id: str | None = os.getenv("ALTEGIO_COMPANY_ID")
    demo_salon_name: str | None = os.getenv("DEMO_SALON")
    cli_client_phone: str | None = os.getenv("CLI_CLIENT_PHONE", "+79001234567")


def get_settings() -> Settings:
    return Settings()
