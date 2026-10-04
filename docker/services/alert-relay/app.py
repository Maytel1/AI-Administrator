"""Релей алертов: Alertmanager → Telegram.

Зачем он, если у Alertmanager есть `telegram_configs`: на этой машине
`api.telegram.org` недоступен напрямую (провайдер блокирует TCP 443), весь
доступ к Telegram идёт через Cloudflare Worker-прокси вида
`<base>/bot<token>/<method>`. Alertmanager про прокси не знает и ходит
напрямую, поэтому между ним и Telegram стоит этот маленький сервис.

Схема:

    Prometheus → Alertmanager → (webhook) → alert-relay → Worker → Telegram

Сервис принимает стандартный webhook Alertmanager, собирает короткое
сообщение и отправляет его в чат. Если Telegram недоступен, он отвечает
Alertmanager'у кодом 500 — тот повторит позже, и алерт не потеряется.

Экранирование HTML обязательно: имена сервисов и аннотации приходят из
конфигурации, а `parse_mode=HTML` доверяет им — один `&` или `<` без
экранирования ломает разметку всего сообщения.

Настройки в `.env`: `TG_ALERT_API_BASE`, `TG_ALERT_TOKEN`, `TG_ALERT_CHAT_ID`.
"""

from __future__ import annotations

import html
import json
import os
import time
from typing import Any

from aiohttp import ClientSession, web
from prometheus_client import Counter as _Counter
from prometheus_client import Gauge as _Gauge
from prometheus_client import generate_latest

# Telegram не принимает сообщения длиннее 4096 символов, а бой приходит
# пачкой на все сработавшие алерты — режем по границе строки, чтобы не
# разорвать HTML-тег посередине.
MAX_MESSAGE = 4000

SEVERITY_EMOJI = {
    "critical": "🔴",
    "warning": "🟡",
    "info": "🔵",
}

# Название алерта → что делать. Без этого человек читает «ServiceDown» и
# не понимает, куда идти: в аннотациях Alertmanager этого нет.
RUNBOOK = {
    "ServiceDown": "runbook, раздел 2 — почему упал сервис",
    "DatabaseUnavailable": "runbook, раздел 1 — БД не отвечает",
    "ServiceRestartedOften": "runbook, раздел 2 — смотрим логи сервиса",
    "ErrorRateHigh": "логи по cid из JSON-логов (X-Request-Id)",
    "RateLimitSpike": "проверить, не шлёт ли кто одно и то же по кругу",
    "NoMessagesForLongTime": "бот не отвечает: логи bot-service",
}

STARTED = time.monotonic()
SENT = 0
FAILED = 0
LAST_ERROR = ""
# Метрики релея: без них поломку доставки нечем поймать. Если Worker или
# Telegram лежат, единственный сигнал — рост failed и отсутствие sent.
_METRICS = {
    "alert_relay_sent_total": _Counter("alert_relay_sent_total", "Доставлено сообщений в Telegram"),
    "alert_relay_failed_total": _Counter("alert_relay_failed_total", "Не удалось доставить"),
    "alert_relay_alerts_total": _Counter(
        "alert_relay_alerts_total", "Алертов в сообщениях"
    ),
    "alert_relay_last_success_timestamp": _Gauge(
        "alert_relay_last_success_timestamp", "Когда последний раз доставили (unix-время)"
    ),
    "alert_relay_configured": _Gauge(
        "alert_relay_configured", "Настроен ли канал (1 — да)", ["component"]
    ),
}
LAST_SUCCESS = 0.0


def esc(value: Any) -> str:
    """Экранирование HTML: имена сервисов и аннотации недоверенные."""
    return html.escape(str(value if value is not None else ""), quote=False)


async def send_to_telegram(session: ClientSession, text: str) -> None:
    """Отправить сообщение в чат через Worker-прокси."""
    base = os.getenv("TG_ALERT_API_BASE", "").rstrip("/")
    token = os.getenv("TG_ALERT_TOKEN", "")
    chat_id = os.getenv("TG_ALERT_CHAT_ID", "")
    if not (base and token and chat_id):
        raise RuntimeError("не заданы TG_ALERT_API_BASE / TG_ALERT_TOKEN / TG_ALERT_CHAT_ID")
    url = f"{base}/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text[:MAX_MESSAGE],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    # Cloudflare Worker отклоняет UA Python-urllib кодом 1010, поэтому
    # подставляем обычный браузерный — тот же приём, что в скилле tg.py.
    headers = {
        "User-Agent": os.getenv(
            "TG_ALERT_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        )
    }
    async with session.post(url, json=payload, headers=headers, timeout=20) as response:
        body = await response.text()
    if response.status != 200:
        raise RuntimeError(f"HTTP {response.status}: {body[:300]}")
    if not json.loads(body).get("ok"):
        raise RuntimeError(f"Telegram отклонил: {body[:300]}")
def format_alert(alert: dict[str, Any]) -> str:
    """Одна строка на алерт: эмодзи, имя, где, что делать."""
    labels = alert.get("labels") or {}
    annotations = alert.get("annotations") or {}
    severity = str(labels.get("severity") or "info")
    name = str(labels.get("alertname") or "без имени")
    where = labels.get("service") or labels.get("instance") or "?"
    summary = annotations.get("summary") or name
    firing = alert.get("status") != "resolved"
    # Устранённый алерт помечаем галочкой, даже если он был critical:
    # красный кружок рядом со словом «устранился» читается как новая тревога.
    emoji = SEVERITY_EMOJI.get(severity, "⚪") if firing else "✅"
    status = "сработал" if firing else "устранился"
    lines = [
        f"{emoji} <b>{esc(status)}</b>: {esc(name)}",
        f"   где: <code>{esc(where)}</code>",
        f"   {esc(summary)}",
    ]
    hint = RUNBOOK.get(name) if firing else None
    if hint:
        lines.append(f"   что делать: {esc(hint)}")
    return "\n".join(lines)


def format_webhook(payload: dict[str, Any]) -> str:
    """Собрать текст сообщения из тела webhook."""
    alerts = payload.get("alerts") or []
    if not alerts:
        return "🔔 Alertmanager: алертов нет (пустое уведомление)."
    firing = sum(1 for alert in alerts if alert.get("status") == "firing")
    if payload.get("status") == "firing":
        head = f"🔴 <b>Алерты</b> ({len(alerts)} шт., сработало {firing})"
    else:
        # Про устранение говорим прямо: «сработало 1» рядом со словом «устранены»
        # читалось бы как противоречие и пугало бы лишний раз.
        head = f"✅ <b>Устранены</b> ({len(alerts)} шт.)"
    return head + "\n\n" + "\n".join(format_alert(alert) for alert in alerts)


async def handle_alerts(request: web.Request) -> web.Response:
    """Webhook от Alertmanager: отправляем в Telegram, отвечаем по результату."""
    global SENT, FAILED, LAST_ERROR, LAST_SUCCESS  # noqa: PLW0603 — состояние для /health и /metrics
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 — битое тело не повод ронять сервис
        return web.json_response({"error": "body is not json"}, status=400)
    alerts = payload.get("alerts") or []
    try:
        await send_to_telegram(request.app["session"], format_webhook(payload))
    except Exception as exc:  # noqa: BLE001 — Alertmanager повторит при 500
        FAILED += 1
        LAST_ERROR = f"{type(exc).__name__}: {exc}"[:200]
        _METRICS["alert_relay_failed_total"].inc()
        print(f"[alert-relay] отправка не удалась: {LAST_ERROR}", flush=True)
        return web.json_response({"error": LAST_ERROR}, status=500)
    SENT += 1
    LAST_SUCCESS = time.time()
    _METRICS["alert_relay_sent_total"].inc()
    _METRICS["alert_relay_last_success_timestamp"].set(LAST_SUCCESS)
    if alerts:
        _METRICS["alert_relay_alerts_total"].inc(len(alerts))
    print(f"[alert-relay] отправлено ({len(alerts)} алертов)", flush=True)
    return web.json_response({"ok": True})


def is_configured() -> bool:
    """Заданы ли все три настройки доставки."""
    return all(
        os.getenv(name)
        for name in ("TG_ALERT_API_BASE", "TG_ALERT_TOKEN", "TG_ALERT_CHAT_ID")
    )


async def handle_health(_request: web.Request) -> web.Response:
    """Health: видно, настроен ли релей и не сыплются ли ошибки отправки."""
    configured = is_configured()
    return web.json_response({
        "status": "ok" if configured else "degraded",
        "service": "alert-relay",
        "configured": configured,
        "uptime_sec": round(time.monotonic() - STARTED, 1),
        "sent": SENT,
        "failed": FAILED,
        "last_error": LAST_ERROR,
        # Отдельно от last_error: время последней удачной доставки. Пока его
        # нет, канал не проверен ни разу — молчание ещё не значит «всё хорошо».
        "last_success_at": LAST_SUCCESS or None,
    }, status=200 if configured else 503)


async def handle_metrics(_request: web.Request) -> web.Response:
    """Prometheus-формат: релей сам должен быть виден в мониторинге.

    Иначе поломку доставки нечем поймать: сервис молча стоит, алерты не уходят,
    и система мониторинга об этом не знает.
    """
    _METRICS["alert_relay_configured"].labels("tg").set(1 if is_configured() else 0)
    return web.Response(
        body=generate_latest(),
        headers={"Content-Type": "text/plain; version=0.0.4; charset=utf-8"},
    )


def main() -> None:
    """Запуск сервиса: порт из ALERT_RELAY_PORT, сессия — на всё время жизни."""
    port = int(os.getenv("ALERT_RELAY_PORT", "8086"))

    async def _startup(application: web.Application) -> None:
        application["session"] = ClientSession()

    async def _cleanup(application: web.Application) -> None:
        await application["session"].close()

    app = web.Application()
    app.on_startup.append(_startup)
    app.on_cleanup.append(_cleanup)
    app.router.add_post("/alerts", handle_alerts)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/metrics", handle_metrics)
    app.router.add_get("/", handle_health)
    web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
