"""Структурированные логи: JSON, correlation-id и безопасность значений."""

import json
import logging

import pytest

from admin_core.logging_setup import (
    log_event,
    new_request_id,
    request_id,
    setup_logging,
)


def _capture(service: str = "svc") -> tuple:
    """Подменяем вывод логгера на буфер: caplog не видит записи"""
    import io  # noqa: PLC0415

    log = setup_logging(service)
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(log.handlers[0].formatter)
    log.handlers = [handler]
    return log, buffer



def test_log_line_is_json_with_required_fields() -> None:
    """Каждая запись — одна строка JSON с временем, уровнем и сервисом."""
    log, buffer = _capture("test-service")
    log_event(log, logging.INFO, "что-то случилось", amount=1990)
    payload = json.loads(buffer.getvalue().strip().splitlines()[-1])
    assert payload["service"] == "test-service"
    assert payload["msg"] == "что-то случилось"
    assert payload["amount"] == 1990
    assert payload["ts"] and payload["level"] == "INFO"


def test_correlation_id_flows_through_context() -> None:
    """Идентификатор запроса попадает в каждую запись внутри контекста."""
    log, buffer = _capture()
    token = request_id.set("cid-42")
    try:
        log_event(log, logging.INFO, "первая")
        log_event(log, logging.INFO, "вторая")
    finally:
        request_id.reset(token)
    parsed = [json.loads(line) for line in buffer.getvalue().strip().splitlines()]
    assert [entry["cid"] for entry in parsed] == ["cid-42"] * 2


def test_request_ids_are_unique_and_readable() -> None:
    """Идентификаторы не повторяются и читаются в логе глазом."""
    ids = {new_request_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(len(value) >= 10 for value in ids)


def test_extra_values_survive_unserializable_objects() -> None:
    """Странное значение в полях не должно ронять запись лога."""
    from decimal import Decimal  # noqa: PLC0415

    log, buffer = _capture()
    log_event(log, logging.INFO, "счёт", amount=Decimal("1990.00"))
    payload = json.loads(buffer.getvalue().strip().splitlines()[-1])
    assert str(payload["amount"]) == "1990.00"


def test_setup_is_idempotent() -> None:
    """Повторная настройка не плодит обработчики: строки не задваиваются."""
    log = setup_logging("svc")
    count = len(log.handlers)
    setup_logging("svc")
    assert len(log.handlers) == count

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
