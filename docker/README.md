# Runbook: что делать, когда что-то сломалось

Инструкции для дежурного, а не для разработчика. Каждая начинается с
«посмотри сюда», потом — «сделай это», и только потом — «если не помогло».

Правило номер один: **сначала бэкап, потом любые ручные правки БД.**
Восстановление из дампа дешевле, чем разбор последствий эксперимента на проде.

## 0. За 60 секунд: что вообще сломано

```
cd docker
docker compose ps                 # кто Up, кто Restarting, кто Exited
docker compose logs --tail 30     # последние строки всех сервисов
```

Метрики и алерты: раздел 10 ниже.

Проверка живых сервисов (каждый отвечает JSON; пусто = сервис мёртв):

| Что | Команда | Норма |
|---|---|---|
| Бот (Telegram/WhatsApp) | `curl http://localhost:8080/health` | `"db": "ok"` |
| Панель | `curl http://localhost:8083/health` | `"status": "ok"` |
| Worker | `curl http://localhost:8084/health` | `"db": "ok"` |
| Автоответчик | `curl http://localhost:8085/health` | `"db": "ok"` |
| call-service API | `curl http://localhost:8081/health` | `unauthorized` — это норма: порт требует токен |

В **проде** эти порты снаружи закрыты (видно только nginx), поэтому проверять
изнутри: `docker compose exec web wget -qO- http://127.0.0.1:8081/health`.

Что означают значения:
- `"db": "down"` → БД недоступна, идём в раздел 1;
- `"status": "unhealthy"` → сервис жив, но сам сообщает о проблеме;
- пусто / таймаут → сервис упал, идём в раздел 2.

## 1. БД не поднимается или отвечает медленно

Симптом: у всех сервисов `"db": "down"`, в логах `Connection refused` на 5432.

```
docker compose ps postgres             # должен быть Up (healthy)
docker compose logs --tail 50 postgres
docker compose restart postgres
```

Если не помогло — **проверьте место на диске**, это самая частая причина:

```
docker system df
docker exec ai-admin-postgres df -h /var/lib/postgresql/data
```

Если диск заполнен, сначала **не удаляйте данные вручную** — сбросьте вес:
`docker system prune` (контейнеры остановятся, volume с данными останется).

Если БД поднялась, но данные «пропали» — не паникуйте, проверьте бэкап
(раздел 4) и **не накатывайте миграции на пустую базу**.

## 2. Сервис упал (контейнер в Restarting)

```
docker compose logs --tail 80 <сервис>     # web | worker | bot-service | call-service
docker compose restart <сервис>
```

Частые причины по убыванию частоты:

1. **Не применены миграции.** Симптом в логах — ошибка вида
   `relation "..." does not exist`. Лечится:
   `docker compose run --rm cli python -m run_migrations`.
2. **Нет ключей в `.env`.** Сервис печатает `not set` в отчёте конфигурации.
   Ничего не «додумывайте» — добавьте ключ в `docker/.env` и перезапустите.
3. **Порт занят другим процессом.** Проверка: `netstat -ano | findstr :8082`.

Если сервис падает сразу после старта и в логах нет внятной ошибки —
увеличьте verbosity: `LOG_LEVEL=DEBUG` в `.env`, затем `docker compose up -d`.

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
| web | ai-admin-web | 8082→8080, 8083→8081 | **Фаза 4:** веб-панель точки — http://localhost:8082/today |

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
## Веб-панель (фаза 4)

`docker compose up -d web` → http://localhost:8082/today, пароль — `WEB_PANEL_PASSWORD`
в `.env` (если переменная не задана, вход закрыт полностью). Разделы: Сегодня, Записи,
Клиенты, Прайс, Мастера, Диалоги, Аудит, Настройки. MVP только читает данные
(правки расписания и услуг — следующий шаг фазы 4). `/health` и `/metrics` — на 8083.
Разбор референса и дорожная карта панели: `ADMIN_PANEL.md`.

## Telegram-бот (фаза 2)

```
docker compose run --rm cli python cli.py --seed    # если БД пустая (нужен tenant)
docker compose up -d bot-service
docker compose logs -f bot-service
```

- В РФ `api.telegram.org` недоступен напрямую (проверено: `Connect call failed ('149.154.167.99', 443)`),
  поэтому бот ходит через прокси **Cloudflare Worker**: в `docker/.env` → `TG_API_BASE=https://<worker>.workers.dev`.
  Код подставляет шаблоны сам: `TelegramAPIServer.from_base(TG_API_BASE)` → `/bot{token}/{method}` и `/file/bot{token}/{path}`.
- Проверка прокси без токена: `curl https://<worker>.workers.dev/bot123456789:FakeToken/getMe`
  → `{"ok":false,"error_code":401,"description":"Unauthorized"}` (это ответ Telegram, значит прокси жив).
- Успешный старт в логах: `[bot] Telegram API: https://<worker>.workers.dev` и `[bot] polling as @<username> (LLM: fake)`.
- **Наблюдаемость:** бот поднимает HTTP на `BOT_HTTP_PORT` (по умолчанию 8080, порт проброшен в compose):
  `curl http://localhost:8080/health` → JSON `{status, db, llm, uptime_sec, chat_sessions}` (503 при недоступной БД),
  `curl http://localhost:8080/metrics` → Prometheus-формат (сообщения, ошибки, rate-limit, аптайм) — `admin_core/health.py`.
- **Rate-limit/антиспам:** `TG_RATE_LIMIT` / `TG_RATE_WINDOW` в `.env` (по умолчанию 6 сообщений за 10 с на чат;
  повторы одного текста и слишком длинные сообщения отсекаются) — `admin_core/ratelimit.py`, тесты `tests/test_ratelimit.py`.
- Токен бота лежит только в `docker/.env` (в git не попадает) и в логи не выводится — печатается лишь имя бота.
- `pywa` (WhatsApp) добавится на тот же роутер, когда подтвердится легальность WhatsApp Business API в РФ.

## 3. Не пускает в панель / «Неверный логин или пароль»

```
# 1) есть ли вообще учётная запись
docker compose exec postgres psql -U aiadmin -d aiadmin \
  -c "SELECT login, role, is_active, totp_enabled FROM panel_users"

# 2) сброс пароля
docker compose run --rm cli python cli.py --user <login> --password <новый> \
  --role <owner|staff|team> --salon "<название салона>"
```

Важно: `--salon` ждёт **название**, а не ID салона.

Если вход верный, а панель всё равно отдаёт форму входа — почти всегда
истекла блокировка по rate-limit: 5 неудачных попыток на логин дают 429
на 15 минут. **Подождите 15 минут**, пароль подбирать не нужно.

Сбросить счётчик можно только перезапуском процесса панели:
`docker compose restart web`.

## 4. Восстановление из бэкапа

Бэкап делает `scripts/backup_db.ps1` (проверен: дамп ~400 КБ, `pg_restore --list` проходит).

Последний дамп:
```
dir docker\backups\*.dump | Sort-Object Name -Descending | Select-Object -First 3
```

Восстановление (скрипт сам требует подтверждения):
```
powershell -ExecutionPolicy Bypass -File scripts\restore_db.ps1 -Dump "docker\backups\<файл>.dump"
```

Проверить, что дамп вообще пригоден, **до** восстановления в прод:
```
docker compose exec -T postgres pg_restore --list < путь/дамп.dump
```

> На Windows скрипты запускаются с `-ExecutionPolicy Bypass`: без него
> PowerShell откажется выполнять неподписанный файл.

## 5. Панель открывается по HTTP, а надо HTTPS / истёк сертификат

Симптом: браузер предупреждает о сертификате либо редирект на HTTP не работает.

1. Проверьте, что сертификат и ключ лежат в `docker/certs/`
   (`fullchain.pem`, `privkey.pem`) и что их срок не истёк.
2. Прод запускается с двумя файлами compose:
   ```
   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
   ```
3. Проверка: `powershell -ExecutionPolicy Bypass -File scripts\check_prod_tls.ps1`.

**HSTS включайте только после того, как HTTPS заработал на реальном домене:**
браузер запоминает его на год, и `http://localhost` перестанет открываться.

Если панель в проде доступна снаружи по HTTP — это не «не настроено»,
это дыра. Порт панели в прод-композе снят (`ports: !reset []`) специально:
наружу его не публикуют, чтобы `WEB_TRUST_PROXY=1` нельзя было обмануть.

## 6. Бот перестал отвечать (Telegram/WhatsApp)

```
docker compose logs --tail 50 bot-service
curl http://localhost:8080/metrics | findstr /i telegram
```

Проверьте, что прокси жив (в РФ `api.telegram.org` недоступен напрямую):
```
curl https://<worker>.workers.dev/bot123456789:FakeToken/getMe
```
Ответ `{"ok":false,"error_code":401}` — это хорошо: значит, прокси отвечает,
токен неверный. Если соединение не устанавливается — проблема в прокси.

## 7. Откат миграции

Если новая миграция сломала прод, откатываем **код и данные** вместе:

```
docker compose run --rm cli python scripts/run_migrations.py --rollback 1
```

Скрипт выполнит парный файл `<имя>.rollback.sql` и уберёт запись из
`schema_migrations`. Если файла отката нет — миграция неоткатываема,
единственный путь: восстановление из бэкапа (раздел 4).

**Перед откатом** посмотрите, что именно будет откатывано:
`docker compose run --rm cli python scripts/run_migrations.py --status`.

## 8. Всё сломалось, времени мало

Порядок действий, который не усугубляет ситуацию:

1. `docker compose ps` — понять, кто упал.
2. Если упала БД → раздел 1. Если сервис → раздел 2.
3. **Не трогайте БД руками**, пока не снят бэкап: `backup_db.ps1`.
4. Не меняйте два сервиса одновременно: после каждого шага — `docker compose ps`.
5. Сомневаетесь — откатывайтесь на последний зелёный CI:

```
gh run list --limit 5
git log --oneline -5
```

## 9. Проверка перед выкаткой

Прогон всех проверок одной командой (те же шаги, что в CI):
```
python verify.py
```

Он же покажет, зелёный ли последний прогон GitHub Actions: `python verify.py --ci`.

## Кого звать

| Симптом | К кому |
|---|---|
| Бот молчит, панель работает | Telegram/WhatsApp: прокси Cloudflare, токен, Meta |
| Панель молчит | web-сервис, nginx, сертификат |
| Всё молчит | postgres, потом хост: диск, память |
| Перестало работать после выкатки | Откат (раздел 7), потом разбор |

## 10. Метрики и алерты

Prometheus живёт в отдельном профиле, чтобы не жесть память, когда он не нужен:

```
docker compose --profile monitoring up -d prometheus
```

Веб-интерфейс: <http://localhost:9090>. Проверить, что все сервисы видны:

```
curl http://localhost:9090/api/v1/targets?state=active
```

Что собирается:

| Метрика | Что показывает |
|---|---|
| `service_up{service}` | сервис поднят |
| `service_db_up{service}` | **отвечает ли БД** — отличает «упал сервис» от «упала БД» |
| `service_uptime_seconds` | аптайм (по нему видно рестарты) |
| `service_messages_total` | обработано сообщений |
| `service_errors_total{where}` | ошибки по местам (`csrf`, `forbidden`, `login_failed`…) |
| `service_rate_limited_total{reason}` | отклонено антиспамом |

Правила алертов — `docker/monitoring/alerts.yml`, шесть штук:

| Алерт | Когда | Что делать |
|---|---|---|
| `ServiceDown` | нет метрик 2 мин | runbook, раздел 2 |
| `DatabaseUnavailable` | `service_db_up == 0` | runbook, раздел 1 |
| `ServiceRestartedOften` | >3 рестарта бота в час | логи, раздел 2 |
| `ErrorRateHigh` | >5 ошибок за 5 мин | логи по `cid` из JSON-логов |
| `RateLimitSpike` | много отклонений антиспамом | один клиент шлёт по кругу |
| `NoMessagesForLongTime` | полчаса тишины | бот перестал отвечать |

Проверить правила без запуска Prometheus:

```
docker run --rm --entrypoint promtool -v %CD%\monitoring:/etc/prometheus prom/prometheus:latest check config /etc/prometheus/prometheus.yml
```

### Куда приходят алерты

Схема доставки:

```
Prometheus → Alertmanager → alert-relay → Worker-прокси → Telegram
```

**Почему не `telegram_configs`:** прямой `api.telegram.org` с этой машины
недоступен (провайдер блокирует TCP 443), весь Telegram ходит через
Cloudflare Worker. Alertmanager про прокси не знает, поэтому между ним и
Telegram стоит `alert-relay` — маленький сервис, который превращает webhook
Alertmanager в сообщение и отправляет его через Worker.

Настройки в `.env` (`docker/.env`, в git не попадают):

```
TG_ALERT_API_BASE=https://<worker>.workers.dev
TG_ALERT_TOKEN=<токен бота>
TG_ALERT_CHAT_ID=<id чата>
```

Пока стоит тот же бот и чат, что у `telegram-relay`. Когда появится
отдельный бот для алертов — достаточно поменять три эти строки и сделать
`docker compose --profile monitoring up -d alert-relay`; больше ничего
трогать не нужно.

Что придёт в сообщение: имя алерта, где сработал, текст аннотации и
строка «что делать» со ссылкой на раздел runbook.

Маршрутизация в `alertmanager.yml`:

| Severity | Куда | Как часто повторять |
|---|---|---|
| `critical` | Telegram | каждый час, при устранении тоже сообщение |
| `warning` | Telegram | при устранении молчит, повтор — раз в сутки |
| остальное | в интерфейс Prometheus | — |

Подавление: пока `DatabaseUnavailable` горит, сообщения `ServiceDown` не
приходят — иначе в Telegram летел бы сам, следствие и следствие следствия.

Проверить доставку, не дожидаясь аварии:

```
curl -X POST -H "Content-Type: application/json" --data-binary "@test-alert.json" \
  http://localhost:9093/api/v2/alerts
curl http://localhost:8087/health     # sent и failed
```

Готовый пример тела — `docker/test-alert.json`. Либо одной командой со
сторожем доставки: `python scripts/check_alert_channel.py` (раздел ниже).

Состояние релея: `sent` — доставлено, `failed` — нет. Не доставленный
алерт Alertmanager повторит сам, поэтому в `failed` смотреть надо с
`last_error`.

### Контур мониторинга под наблюдением

Метрики собираются не только с четырёх прикладных сервисов, но и с самого
Alertmanager и релея (`job: monitoring`). Без этого «всё спокойно» и
«никто ничего не отправляет» выглядели бы одинаково — а это худший вид
отказа, потому что обнаруживается он постфактум.

Метрики релея:

| Метрика | Что показывает |
|---|---|
| `alert_relay_sent_total` | доставлено сообщений |
| `alert_relay_failed_total` | не удалось доставить |
| `alert_relay_last_success_timestamp` | когда последний раз доставили (0 = ни разу) |
| `alert_relay_configured` | заданы ли `TG_ALERT_*` (1 — да) |

Правила, которые ловят поломку самого мониторинга:

| Алерт | Когда | Что делать |
|---|---|---|
| `MonitoringDead` | Alertmanager или релей недоступны 3 мин | `docker compose ps`, логи |
| `AlertRelayNotConfigured` | нет `TG_ALERT_*` | заполнить `.env` |
| `AlertDeliveryFailed` | релей ответил 500 — сообщение потеряно | токен, Worker, сеть |
| `AlertRelayUntested` | 6 часов ни одной доставки | проверить канал вручную |

### Проверка правил

Правило с опечаткой в выражении молчит ровно тогда, когда всё сломалось,
поэтому каждое проверяется unit-тестом на подставных рядах — без аварий
в стенде (`docker/monitoring/alerts_test.yml`, 8 сценариев). Запуск:

```
docker run --rm --entrypoint promtool ^
  -v %CD%\monitoring:/etc/prometheus prom/prometheus:latest ^
  test rules /etc/prometheus/alerts_test.yml
```

Проверяется и то, что алерт **гаснет**, когда поломка ушла: вечно висящее
правило перестают читать. Оба шага входят в `verify.py` и в CI.

### Проверка канала вручную

```
python scripts/check_alert_channel.py
```

Скрипт отправляет служебный алерт через Alertmanager (то есть по всей
цепочке, а не напрямую в релей) и ждёт подтверждения доставки. Код возврата:
0 — работает, 1 — нет, 2 — не настроен. Годится для планировщика: молчаливый
успех невозможен, задача завершится с ошибкой.

---

# Docker — локальный dev-стек (фаза 0)

1. **Фаза 1** — схема БД (миграции в `postgres/init/`), CRM-адаптер (Altegio/YCLIENTS), LLM-агент, CLI-режим диалога. Критерий: 90% тестовых диалогов пишут запись в CRM.
2. **Фаза 2** — `aiogram` в bot-service (эхо-бот), публичный HTTPS для вебхуков (или polling локально), подтверждающие SMS/мессенджер-уведомления.
3. **Фаза 3** — Pipecat + SpeechKit + SIP в call-service; failover по `FAILOVER_SCENARIOS.md`.
4. **Фаза 4+** — worker: outbox, напоминания, биллинг; при росте — перенос на Managed PG/K8s (раздел 1.3 плана).

## Ключи и безопасность

- Секреты — только в `.env` (вне git) → позже Yandex Lockbox (фаза 2–3).
- Отчёт конфигурации печатает только факт «set / not set», значения ключей не логируются.
