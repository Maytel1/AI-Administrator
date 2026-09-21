"""Фабрика провайдеров по настройкам.

LLM_PROVIDER=fake        — работает без ключей (демо и тесты, детерминированный сценарий)
LLM_PROVIDER=yandexgpt   — после создания YC-аккаунта (YC_API_KEY, YC_FOLDER_ID)
LLM_PROVIDER=openai      — gpt-4o-mini; нужен OPENAI_API_KEY (для РФ — посредник)
"""

from ..config import Settings, get_settings
from .base import LLMProvider


def make_provider(settings: Settings | None = None) -> LLMProvider:
    s = settings or get_settings()
    if s.llm_provider == "fake":
        from .fake import FakeProvider

        return FakeProvider()
    if s.llm_provider == "yandexgpt":
        from .yandex import YandexGPTProvider

        if not (s.yc_api_key and (s.yandexgpt_model_uri or s.yc_folder_id)):
            raise RuntimeError(
                "LLM_PROVIDER=yandexgpt, но не заданы YC_API_KEY и "
                "(YANDEXGPT_MODEL_URI или YC_FOLDER_ID) — добавьте в docker/.env"
            )
        return YandexGPTProvider(
            api_key=s.yc_api_key,
            folder_id=s.yc_folder_id,
            model_uri=s.yandexgpt_model_uri,
        )
    if s.llm_provider in ("openai", "openai_compat"):
        from .openai_compat import OpenAICompatProvider

        if not s.openai_api_key:
            raise RuntimeError(
                "LLM_PROVIDER=openai, но не задан OPENAI_API_KEY — добавьте в docker/.env"
            )
        return OpenAICompatProvider(
            api_key=s.openai_api_key,
            base_url=s.openai_base_url,
            model=s.openai_model,
        )
    raise RuntimeError(f"Неизвестный LLM_PROVIDER: {s.llm_provider}")
