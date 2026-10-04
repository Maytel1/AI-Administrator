"""Релей алертов: форматирование сообщений и экранирование.

Проверяем без сети — чистые функции. Реальная отправка проверяется на
живом стенде: POST в /alerts и счётчики в /health.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

# Модуль грузим по пути файла, а не через sys.path: пакет `app` в образе уже
# занят веб-сервисом панели, и обычный `import app` притащил бы его.
#
# Путь ищем в двух местах: репозиторий (services/alert-relay рядом с core)
# и образ (services/alert-relay скопирован в /app для тестов).
_CANDIDATES = (
    Path(__file__).resolve().parents[2] / "services" / "alert-relay" / "app.py",
    Path("/app/services/alert-relay/app.py"),
)
_RELAY_PATH = next((path for path in _CANDIDATES if path.is_file()), None)
assert _RELAY_PATH is not None, f"не найден alert-relay/app.py: проверьте {_CANDIDATES}"
_spec = importlib.util.spec_from_file_location("alert_relay_app", _RELAY_PATH)
assert _spec is not None and _spec.loader is not None
relay: ModuleType = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(relay)
esc = relay.esc
format_alert = relay.format_alert
format_webhook = relay.format_webhook


def test_webhook_renders_alert_with_runbook_hint() -> None:
    """Критичный алерт: имя, где, что делать — всё в одном сообщении."""
    text = format_webhook({
        "status": "firing",
        "alerts": [{
            "status": "firing",
            "labels": {"alertname": "DatabaseUnavailable", "severity": "critical", "service": "web-service"},
            "annotations": {"summary": "БД недоступна"},
        }],
    })
    assert "DatabaseUnavailable" in text
    assert "web-service" in text
    assert "БД недоступна" in text
    # Подсказка из runbook — главная ценность сообщения: без неё «что делать»
    # приходится выдумывать в три часа ночи.
    assert "runbook" in text
    assert "🔴" in text


def test_resolved_alert_marked_and_not_critical_emoji() -> None:
    """Устранённый алерт должен быть виден как устранённый, а не как сработавший."""
    text = format_webhook({
        "status": "resolved",
        "alerts": [{
            "status": "resolved",
            "labels": {"alertname": "ServiceDown", "severity": "critical"},
            "annotations": {},
        }],
    })
    assert "устранился" in text
    assert "сработал" not in text
    assert "✅" in text
    # Заголовок без слова «Алерты» (красный) — устранение не должно выглядеть
    # как тревога, даже если сам алерт в прошлом был critical.
    assert "🔴" not in text


def test_html_is_escaped() -> None:
    """Имена и аннотации недоверенные: без экранирования ломается разметка."""
    text = format_alert({
        "status": "firing",
        "labels": {"alertname": "X <script>", "severity": "warning", "service": "a&b"},
        "annotations": {"summary": "5 < 10 & 10 > 5"},
    })
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "a&b" not in text
    assert "a&amp;b" in text


def test_empty_alerts_do_not_send_nonsense() -> None:
    """Пустой webhook не должен превращаться в сообщение без смысла."""
    assert "нет" in format_webhook({"status": "firing", "alerts": []})


def test_esc_handles_none() -> None:
    assert esc(None) == ""


@pytest.mark.parametrize(
    ("severity", "emoji"),
    [("critical", "🔴"), ("warning", "🟡"), ("info", "🔵"), ("unknown", "⚪")],
)
def test_severity_emoji(severity: str, emoji: str) -> None:
    """Неизвестная severity не должна ломать сообщение — просто нейтральный знак."""
    text = format_alert({
        "status": "firing",
        "labels": {"alertname": "X", "severity": severity},
        "annotations": {},
    })
    assert emoji in text
