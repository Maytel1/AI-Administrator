"""Наблюдаемость: ``/health`` (JSON) и ``/metrics`` (Prometheus text).

Подключение сервисом — одна строка: ``make_health_app(...)`` + ``start_health_server(...)``
(подключено в bot-service; worker и call-service — по мере выхода из скелета,
см. GAP_ANALYSIS §6.4). ``/health`` отвечает 200 при живой БД и 503 (degraded)
при недоступной — готово для healthcheck'а оркестратора и алертов.

Метрики живут в собственной ``CollectorRegistry`` (не в глобальной): процессы
не мешают друг другу, рендер проверяется в тестах без порта и без aiohttp —
он импортируется лениво, внутри фабрики приложения.
"""

from __future__ import annotations

import time
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest


class ServiceMetrics:
    """Счётчики одного процесса сервиса."""

    def __init__(self, service: str, *, registry: CollectorRegistry | None = None) -> None:
        self.service = service
        self.registry = registry or CollectorRegistry()
        reg = self.registry
        self._started = time.monotonic()
        self.up = Gauge("service_up", "Сервис поднят (1)", ["service"], registry=reg)
        self.uptime = Gauge("service_uptime_seconds", "Аптайм, сек", ["service"], registry=reg)
        self.messages = Counter(
            "service_messages_total", "Обработано сообщений", ["kind"], registry=reg
        )
        self.errors = Counter(
            "service_errors_total", "Ошибки обработки", ["where"], registry=reg
        )
        self.rate_limited = Counter(
            "service_rate_limited_total", "Отклонено rate-limiter", ["reason"], registry=reg
        )
        self.up.labels(service).set(1)
        self.uptime.labels(service).set(0)

    def render(self) -> bytes:
        """Prometheus text format ( exposition)."""
        self.uptime.labels(self.service).set(max(0.0, time.monotonic() - self._started))
        return generate_latest(self.registry)


def health_payload(
    *,
    db_ok: bool,
    provider: str | None = None,
    service: str = "service",
    uptime: float = 0.0,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Тело ``/health``: статус, БД, LLM-провайдер, аптайм и расширения."""
    payload: dict[str, Any] = {
        "status": "ok" if db_ok else "degraded",
        "service": service,
        "db": "ok" if db_ok else "error",
        "uptime_sec": round(max(0.0, uptime), 1),
    }
    if provider:
        payload["llm"] = provider
    if extra:
        payload.update(extra)
    return payload


async def ping_db(pool: Any) -> bool:
    """SELECT 1 через пул; любой сбой = БД недоступна."""
    try:
        return await pool.fetchval("SELECT 1") == 1
    except Exception:  # noqa: BLE001 — цель именно «хоть что-то живо?»
        return False


def make_health_app(
    metrics: ServiceMetrics,
    *,
    pool: Any = None,
    provider: str | None = None,
    extra: dict[str, Any] | Any = None,
):
    """aiohttp-приложение с роутами /health и /metrics (aiohttp — лениво)."""
    from aiohttp import web  # noqa: PLC0415 — в cli-контейнере aiohttp нет

    started = time.monotonic()

    async def health(_request):  # noqa: ANN202
        db_ok = await ping_db(pool) if pool is not None else True
        ext = extra() if callable(extra) else extra
        payload = health_payload(
            db_ok=db_ok,
            provider=provider,
            service=metrics.service,
            uptime=time.monotonic() - started,
            extra=ext,
        )
        return web.json_response(payload, status=200 if db_ok else 503)

    async def metrics_endpoint(_request):  # noqa: ANN202
        return web.Response(
            body=metrics.render(),
            headers={"Content-Type": "text/plain; version=0.0.4; charset=utf-8"},
        )

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/metrics", metrics_endpoint)
    return app


async def start_health_server(app: Any, host: str = "0.0.0.0", port: int = 8080) -> Any:
    """Запускает health-сервер фоном; возвращает runner — вызвать cleanup()."""
    from aiohttp import web  # noqa: PLC0415

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    return runner
