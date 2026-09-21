# Docker — локальный dev-стек (фаза 0)

Скелет из DEVELOPMENT_PLAN.md: «docker-compose (call-service, bot-service, worker, PostgreSQL)».
Всё локально, затраты 0 ₽ — платные внешние API (YandexGPT/SpeechKit) скелет не вызывает.

## Быстрый старт

```
cd C:\Users\1\Desktop\project\docker
copy .env.example .env          # ключи в git не попадают (.env в .gitignore)
docker compose up -d            # сборка + запуск
docker compose ps               # 4 контейнера, postgres — healthy
docker compose logs -f bot-service
```

Стоп: `docker compose down` (данные БД остаются в volume `pgdata`).
Полный сброс: `docker compose down -v`. Порт БД наружу — 5433 (не конфликтует с локальным 5432).

## Состав

| Сервис | Контейнер | Порты | Назначение |
|---|---|---|---|
| postgres | ai-admin-postgres | 5433→5432 | PostgreSQL 16-alpine, volume `pgdata`, init-скрипты в `postgres/init/` |
| bot-service | ai-admin-bot | 8080→8080 | скелет. **Фаза 2:** aiogram (Telegram) + pywa (WhatsApp) |
| call-service | ai-admin-call | 8081→8080 | скелет. **Фаза 3:** Pipecat: VAD → SpeechKit STT → LLM → TTS, SIP |
| worker | ai-admin-worker | — | скелет. **Фазы 4–5:** outbox, напоминания, рободо-звон, лимиты тарифа |

Каждый сервис при старте печатает отчёт конфигурации (какие ключи заданы в `.env`) и ждёт PostgreSQL.

## Ядро фазы 1 (core/ + сервис cli)

`core/admin_core/` — общий «мозг»: LLM-провайдеры (`fake` / `yandexgpt` / `openai`),
CRM-адаптеры (`internal` — наш календарь, сценарий A; `altegio` — заготовка по API),
диалоговый агент (system prompt, tools, обработка ошибки 23P01), обезличивание ПДн.

По умолчанию `LLM_PROVIDER=fake` — **полный сценарий записи работает без внешних ключей**.

```
docker compose run --rm cli python cli.py --seed     # демо-данные: салон «Виктория», 2 мастера, 4 услуги
docker compose run --rm cli python cli.py            # диалог в терминале
docker compose run --rm cli python -m pytest -q      # тесты (нужен запущенный postgres)
```

После создания YC-аккаунта: в `docker/.env` → `LLM_PROVIDER=yandexgpt`, `YC_API_KEY`, `YC_FOLDER_ID`.

## Дорога по фазам

1. **Фаза 1** — схема БД (миграции в `postgres/init/`), CRM-адаптер (Altegio/YCLIENTS), LLM-агент, CLI-режим диалога. Критерий: 90% тестовых диалогов пишут запись в CRM.
2. **Фаза 2** — `aiogram` в bot-service (эхо-бот), публичный HTTPS для вебхуков (или polling локально), подтверждающие SMS/мессенджер-уведомления.
3. **Фаза 3** — Pipecat + SpeechKit + SIP в call-service; failover по `FAILOVER_SCENARIOS.md`.
4. **Фаза 4+** — worker: outbox, напоминания, биллинг; при росте — перенос на Managed PG/K8s (раздел 1.3 плана).

## Ключи и безопасность

- Секреты — только в `.env` (вне git) → позже Yandex Lockbox (фаза 2–3).
- Отчёт конфигурации печатает только факт «set / not set», значения ключей не логируются.
