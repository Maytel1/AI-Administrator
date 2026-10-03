"""Структурированные логи с correlation-id.

Зачем: сейчас разбор инцидента — это grep по строкам, а веб, бот и база
говорят в разные моменты времени. Идентификатор запроса связывает их в одну
цепочку: один ``x-request-id`` проходит через веб, CRM и запись в ``audit_log``.

Формат — JSON в одну строку: его понимают и docker, и агрегаторы логов, а
человек в терминале всё ещё видит сообщение.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextvars import ContextVar
from typing import Any

request_id: ContextVar[str] = ContextVar("request_id", default="")

LOGGER_NAME = "ai"


class JsonFormatter(logging.Formatter):
    """Одна строка JSON: время, уровень, сервис, correlation-id, сообщение."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "msg": record.getMessage(),
            "cid": request_id.get() or "-",
        }
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(service: str, *, level: str | None = None) -> logging.Logger:
    """Настроить JSON-логи для сервиса. Вызывается один раз при старте."""
    logger = logging.getLogger(LOGGER_NAME)
    if getattr(logger, "_ai_configured", False):
        return logger
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    logger.handlers = [handler]
    logger.setLevel(str(level or os.getenv("LOG_LEVEL", "INFO")).upper())
    logger.propagate = False
    setattr(logger, "_ai_configured", True)
    return logger


def get_logger(name: str = LOGGER_NAME) -> logging.Logger:
    """Логгер с дополнительными полями: ``log.info("счёт", extra_fields={...})``."""
    return logging.getLogger(name)


def log_event(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    """Событие с полями: ``log_event(log, logging.INFO, "invoice", amount=1990)``."""
    logger.log(level, message, extra={"extra_fields": fields})


def new_request_id() -> str:
    """Идентификатор запроса: время + случай — читаем в логах глазом."""
    import secrets  # noqa: PLC0415

    import time  # noqa: PLC0415
    return f"{int(time.time() * 1000):x}{secrets.token_hex(3)}"
