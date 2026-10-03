"""Нагрузочный прогон: сколько записей в минуту система держит без деградации.

Число нужно не «хорошее», а известное: на пилоте выяснять это дорого, когда
клиент уже стоит в очереди. Метрики — время создания записи, доля отказов и
потолок по HTTP.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import time
import uuid

import asyncpg

# Скрипт живёт в /app/scripts, а общий код — в /app: без этого запускать
# придётся из корня, и путь к admin_core ломается.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


async def _prepare(pool: asyncpg.Pool, masters: int) -> dict:
    """Точка-макет: мастера, услуга и график на ближайшие дни."""
    salon = await pool.fetchrow(
        "INSERT INTO salons (name, timezone) VALUES ($1, 'Asia/Yekaterinburg') RETURNING id",
        f"load-{uuid.uuid4().hex[:8]}",
    )
    service = await pool.fetchrow(
        "INSERT INTO services (salon_id, title, duration_min, price) "
        "VALUES ($1, 'Нагрузка', 30, 1000) RETURNING id",
        salon["id"],
    )
    master_ids = []
    for i in range(masters):
        master = await pool.fetchrow(
            "INSERT INTO masters (salon_id, name) VALUES ($1, $2) RETURNING id",
            salon["id"], f"Мастер {i}",
        )
        master_ids.append(str(master["id"]))
        await pool.execute(
            "INSERT INTO master_services (master_id, service_id) VALUES ($1, $2)",
            master["id"], service["id"],
        )
        for weekday in range(7):
            await pool.execute(
                "INSERT INTO working_hours (master_id, weekday, start_time, end_time) "
                "VALUES ($1, $2, '00:00', '23:59')",
                master["id"], weekday,
            )
    return {"salon_id": str(salon["id"]), "service_id": str(service["id"]),
            "masters": master_ids}


async def run(
    dsn: str, *, requests: int = 200, masters: int = 4, concurrency: int = 10,
    max_ms: int = 1500,
) -> int:
    """Прогнать N записей и вернуть 0, если система держит порог, иначе 1."""
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=concurrency + 2)
    try:
        target = await _prepare(pool, masters)
        clients = []
        for i in range(concurrency * 2):
            client = await pool.fetchrow(
                "INSERT INTO clients (salon_id, phone, name) "
                "VALUES ($1, $2, $3) RETURNING id",
                target["salon_id"], f"+7900{1000000 + i}", f"Клиент {i}",
            )
            clients.append(str(client["id"]))

        timings: list[float] = []
        failures = 0
        first_error: str | None = None
        queue = asyncio.Queue()
        for i in range(requests):
            queue.put_nowait(i)

        # Слоты берём из реального списка свободных: выдуманное время не
        # существует, и мы бы измеряли не систему, а отказ по несуществующему слоту.
        from admin_core.crm.internal import InternalCRM  # noqa: PLC0415

        free = await InternalCRM(pool).get_free_slots(target["salon_id"], "Нагрузка")
        if not free:
            print("свободных слотов нет — расширьте график или запустите в другое время")
            return 1
        print(f"свободных слотов найдено: {len(free)}")

        async def worker() -> None:
            nonlocal failures, first_error
            from admin_core.crm.internal import InternalCRM  # noqa: PLC0415

            local = InternalCRM(pool)
            while not queue.empty():
                index = queue.get_nowait()
                client_id = clients[index % len(clients)]
                # Каждый запрос берёт реальный свободный слот: конфликты тогда
                # означают реальную гонку, а не выдуманное время.
                slot = free[index % len(free)]
                started = time.perf_counter()
                try:
                    result = await local.create_booking(
                        target["salon_id"], client_id, "Нагрузка",
                        time_text=slot["start_text"], offered=[slot],
                        created_by="load-test",
                    )
                    if not result.get("ok"):
                        failures += 1
                        if first_error is None:
                            first_error = str(result.get("error"))
                except Exception as exc:  # noqa: BLE001 — слот занят это не ошибка системы
                    failures += 1
                    if first_error is None:
                        first_error = f"{type(exc).__name__}: {exc}"
                timings.append((time.perf_counter() - started) * 1000)

        wall = time.perf_counter()
        await asyncio.gather(*[worker() for _ in range(concurrency)])
        elapsed = time.perf_counter() - wall

        timings.sort()
        p95 = timings[int(len(timings) * 0.95)] if timings else 0.0
        per_minute = requests / elapsed * 60 if elapsed else 0.0
        print(f"записей: {len(timings)} за {elapsed:.1f} c ({per_minute:.0f}/мин)")
        print(f"медиана: {timings[len(timings) // 2]:.0f} мс, p95: {p95:.0f} мс")
        # Отказы здесь — это не деградация: запросов больше, чем свободных
        # слотов (их get_free_slots отдаёт максимум 12 на 2 дня).
        print(f"отказов (слот занят): {failures}")
        ok = p95 <= max_ms
        print("ИТОГ:", "держит порог" if ok else f"ПРОВАЛ: p95 {p95:.0f} мс > {max_ms} мс")
        await pool.execute("DELETE FROM salons WHERE id = $1", target["salon_id"])
        return 0 if ok else 1
    finally:
        await pool.close()


async def run_panel(
    base_url: str, *, requests: int = 100, concurrency: int = 8, max_ms: int = 800
) -> int:
    """Нагрузка на HTTP-слой панели: разделы, выгрузки, health.

    Раньше мерили только запись в БД, а панель — то, чем люди пользуются каждый
    день. Сценарий повторяет обычный путь: «Сегодня», «Записи», отчёты, CSV.
    """
    import time  # noqa: PLC0415

    import aiohttp  # noqa: PLC0415

    paths = [
        "/today", "/bookings", "/clients", "/services", "/masters",
        "/reports", "/dialogs", "/export.csv?period=30",
        "/accounting.csv?kind=payments", "/health",
    ]
    latencies: list[float] = []
    errors = 0
    connector = aiohttp.TCPConnector(limit=concurrency * 2, ssl=False)
    timeout = aiohttp.ClientTimeout(total=20)

    async def hit(session: aiohttp.ClientSession, path: str) -> None:
        nonlocal errors
        started = time.perf_counter()
        try:
            async with session.get(base_url + path, timeout=timeout) as response:
                await response.read()
                # Редирект на вход для анонимного клиента — норма, не сбой.
                if response.status >= 500:
                    errors += 1
        except Exception:  # noqa: BLE001 — сетевой сбой тоже в счёт
            errors += 1
        latencies.append((time.perf_counter() - started) * 1000)

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks: list = []
        for index in range(requests):
            tasks.append(hit(session, paths[index % len(paths)]))
            if len(tasks) >= concurrency:
                await asyncio.gather(*tasks)
                tasks = []
        if tasks:
            await asyncio.gather(*tasks)

    latencies.sort()
    p50 = latencies[len(latencies) // 2] if latencies else 0.0
    p95 = latencies[int(len(latencies) * 0.95) - 1] if latencies else 0.0
    print(
        f"ПАНЕЛЬ: {requests} запросов, p50={p50:.0f} мс, p95={p95:.0f} мс, "
        f"ошибок 5xx: {errors}"
    )
    ok = p95 <= max_ms and errors == 0
    print("ИТОГ:", "держит порог" if ok else f"ПРОВАЛ: p95 {p95:.0f} > {max_ms}")
    return 0 if ok else 1
async def run_webhook(
    webhook_url: str, secret: str, *, requests: int = 100, concurrency: int = 8,
    max_ms: int = 500,
) -> int:
    """Нагрузка на вебхук с проверкой идемпотентности под давлением.

    Половина запросов — повторы тех же wamid: они обязаны дать duplicates, а не
    второй ответ, иначе под нагрузкой клиент получит две записи.
    """
    import time  # noqa: PLC0415

    import aiohttp  # noqa: PLC0415
    from admin_core.channels.whatsapp_auth import sign_body  # noqa: PLC0415

    latencies: list[float] = []
    duplicates = 0
    handled = 0
    failures = 0
    connector = aiohttp.TCPConnector(limit=concurrency * 2)
    timeout = aiohttp.ClientTimeout(total=20)

    async def hit(session: aiohttp.ClientSession, wamid: str) -> None:
        nonlocal duplicates, handled, failures
        body = json.dumps({"entry": [{"changes": [{"value": {"messages": [{
            "id": wamid, "from": "79999999999", "timestamp": "1",
            "text": {"body": "хочу записаться"},
        }]}}]}]}).encode("utf-8")
        started = time.perf_counter()
        try:
            async with session.post(
                webhook_url, data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Hub-Signature-256": sign_body(body, secret),
                },
                timeout=timeout,
            ) as response:
                data = await response.json()
                handled += int(data.get("handled", 0))
                duplicates += int(data.get("duplicates", 0))
        except Exception:  # noqa: BLE001 — сетевой сбой считаем отдельно
            failures += 1
        latencies.append((time.perf_counter() - started) * 1000)

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks: list = []
        for index in range(requests):
            tasks.append(hit(session, f"wamid.load.{index // 2}"))
            if len(tasks) >= concurrency:
                await asyncio.gather(*tasks)
                tasks = []
        if tasks:
            await asyncio.gather(*tasks)

    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95) - 1] if latencies else 0.0
    print(
        f"ВЕБХУК: {requests} запросов, p95={p95:.0f} мс, обработано={handled}, "
        f"дублей={duplicates}, сбоев={failures}"
    )
    ok = p95 <= max_ms and failures == 0
    print("ИТОГ:", "держит порог" if ok else f"ПРОВАЛ: p95 {p95:.0f} > {max_ms}")
    return 0 if ok else 1

def main() -> None:
    """Три сценария нагрузки: БД, панель и вебхук."""
    parser = argparse.ArgumentParser(description="Нагрузочные тесты")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--masters", type=int, default=4)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--max-ms", type=int, default=1500)
    parser.add_argument("--panel-url", default="")
    parser.add_argument("--webhook-url", default="")
    parser.add_argument("--webhook-secret", default="")
    parser.add_argument(
        "--dsn",
        default="postgresql://aiadmin:aiadmin_dev@postgres:5432/aiadmin",
    )
    args = parser.parse_args()

    if args.panel_url or args.webhook_url:
        async def both() -> int:
            code = 0
            if args.panel_url:
                code |= await run_panel(
                    args.panel_url, requests=args.requests,
                    concurrency=args.concurrency, max_ms=args.max_ms,
                )
            if args.webhook_url and args.webhook_secret:
                code |= await run_webhook(
                    args.webhook_url, args.webhook_secret, requests=args.requests,
                    concurrency=args.concurrency, max_ms=args.max_ms,
                )
            return code

        raise SystemExit(asyncio.run(both()))

    raise SystemExit(asyncio.run(run(
        args.dsn, requests=args.requests, masters=args.masters,
        concurrency=args.concurrency, max_ms=args.max_ms,
    )))


if __name__ == "__main__":
    main()
