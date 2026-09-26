"""Тесты /health payload и рендера /metrics — без порта и без aiohttp."""

from admin_core.health import ServiceMetrics, health_payload, ping_db


def test_payload_ok():
    p = health_payload(db_ok=True, provider="fake", service="bot-service", uptime=1.26)
    assert p["status"] == "ok"
    assert p["db"] == "ok"
    assert p["llm"] == "fake"
    assert p["uptime_sec"] == 1.3


def test_payload_degraded_with_extra():
    p = health_payload(db_ok=False, extra={"chat_sessions": 3})
    assert p["status"] == "degraded"
    assert p["db"] == "error"
    assert p["chat_sessions"] == 3


def test_metrics_render_prometheus_text():
    m = ServiceMetrics("bot-service")
    m.messages.labels("user").inc()
    m.errors.labels("run_turn").inc()
    m.rate_limited.labels("rate").inc()
    text = m.render().decode()
    assert "# TYPE service_messages_total counter" in text
    assert 'service_messages_total{kind="user"' in text
    assert 'service_errors_total{where="run_turn"' in text
    assert 'service_rate_limited_total{reason="rate"' in text
    assert "service_up{" in text


class _FakePool:
    def __init__(self, value: int = 1, fails: bool = False) -> None:
        self._value = value
        self._fails = fails

    async def fetchval(self, _query: str):
        if self._fails:
            raise RuntimeError("db down")
        return self._value


async def test_ping_db_ok():
    assert await ping_db(_FakePool()) is True


async def test_ping_db_down_is_degraded():
    assert await ping_db(_FakePool(fails=True)) is False
