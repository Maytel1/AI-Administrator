# AI-администратор для автосервисов и стоматологий — документы проекта

Продукт: голосовой + текстовый AI-администратор (звонки 24/7, WhatsApp/Telegram,
запись в YCLIENTS/Altegio, AMO CRM, I-dent, 1С:Медицина, Medesk) для автосервисов и
стоматологий Тюмени. LLM-ядро — YandexGPT 5.1 (гибрид Lite/Pro) на стеке Yandex Cloud.

## Как смотреть документы

**GitHub Pages (рекомендуется, 1 минута):** в репозитории откройте
`Settings → Pages → Build and deployment → Source: Deploy from a branch`,
Branch: `main`, папка: `/ (root)` → `Save`. Через ~1 минуту индекс откроется по
адресу `https://<username>.github.io/<repo>/` — все ссылки внутри относительные.

**Локально:** просто откройте `index.html` в браузере.

**Страница файла на github.com:** HTML там отображается исходным кодом — это
нормально. Быстрый просмотр без настройки Pages:
`https://htmlpreview.github.io/?https://github.com/<username>/<repo>/blob/main/index.html`.
Файл `.nojekyll` уже добавлен (отключает сборку Jekyll на Pages).
Для приватного репозитория Pages доступен только на платных планах.

## Документы

| Документ | HTML | MD | Статус |
|---|---|---|---|
| Идеи и концепция | [IDEAS.html](IDEAS.html) | [IDEAS.md](IDEAS.md) | концепция принята |
| План проекта | [PLAN.html](PLAN.html) | [PLAN.md](PLAN.md) | рабочий план |
| Конкуренты Тюмени | [COMPETITORS_TYUMEN.html](COMPETITORS_TYUMEN.html) | [COMPETITORS_TYUMEN.md](COMPETITORS_TYUMEN.md) | отчёт 19.09 |
| План разработки | [DEVELOPMENT_PLAN.html](DEVELOPMENT_PLAN.html) | [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) | v2.1 (26.09.2026) |
| Текстовый бот — оценки | [TEXT_BOT.html](TEXT_BOT.html) | [TEXT_BOT.md](TEXT_BOT.md) | оценки + расчёты 26.09.2026 |
| Голосовой бот — оценки | [VOICE_BOT.html](VOICE_BOT.html) | [VOICE_BOT.md](VOICE_BOT.md) | оценки + расчёты 26.09.2026 |
| Тарифы | [TARIFFS.html](TARIFFS.html) | [TARIFFS.md](TARIFFS.md) | сетка выбрана — Вариант B (29.09) |
| Ответственность | [LIABILITY_OPTIONS.html](LIABILITY_OPTIONS.html) | [LIABILITY_OPTIONS.md](LIABILITY_OPTIONS.md) | ждёт выбора варианта |
| Точка без CRM | [SALON_WITHOUT_CRM.html](SALON_WITHOUT_CRM.html) | [SALON_WITHOUT_CRM.md](SALON_WITHOUT_CRM.md) | выбран A + D (29.09) |
| ПДн и LLM | [PDN_LLM_OPTIONS.html](PDN_LLM_OPTIONS.html) | [PDN_LLM_OPTIONS.md](PDN_LLM_OPTIONS.md) | ждёт выбора варианта |
| Договор ПДн | [CONTRACT_PDN_TEMPLATE.html](CONTRACT_PDN_TEMPLATE.html) | [CONTRACT_PDN_TEMPLATE.md](CONTRACT_PDN_TEMPLATE.md) | проверить у юриста |
| Сбои и failover | [FAILOVER_SCENARIOS.html](FAILOVER_SCENARIOS.html) | [FAILOVER_SCENARIOS.md](FAILOVER_SCENARIOS.md) | перед фазой 3 |
| Открытые вопросы | [OPEN_QUESTIONS.html](OPEN_QUESTIONS.html) | [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) | парк вопросов (29) |
| Аудит проекта | [GAP_ANALYSIS.html](GAP_ANALYSIS.html) | [GAP_ANALYSIS.md](GAP_ANALYSIS.md) | новый · 26.09.2026 |
| TAM Тюмень | [TAM_TYUMEN.html](TAM_TYUMEN.html) | [TAM_TYUMEN.md](TAM_TYUMEN.md) | отчёт · 29.09.2026 |

## Лендинг «Дежурный24» (прототип)

Папка `landing/` — статический сайт-визитка (техника A, без зависимостей):

```bash
# локальный просмотр (или просто открыть landing/index.html в браузере)
python -m http.server 8090 --directory landing
```

Файлы: `index.html` (все 4 концепции: демо-CTA, чат Анны, табы вертикалей, калькулятор),
`styles.css`, `app.js`, `privacy.html` (заготовка Политики ПДн — на проверку юриста).
Публикация: после покупки `dezhurny24.ru` — CNAME на GitHub Pages (папка `landing/`).

## Перегенерация

```bash
python build_html_docs.py     # 14 документов md -> html + валидация разметки
python validate_plan_html.py  # паритет DEVELOPMENT_PLAN.html ↔ DEVELOPMENT_PLAN.md
```

Markdown — источник истины (`DEVELOPMENT_PLAN.html` генерируется из MD: раздел,
таблицы и чек-листы в HTML совпадают с MD). Нумерация секций берётся из MD, поэтому
ссылки вида «§4.4» в тексте указывают на тот же раздел и в HTML.
`index.html` и этот README обновляются вручную при добавлении новых документов.

## Контроль качества и CI

```bash
# линтер и типы (локально):
pip install ruff mypy
ruff check docker                                  # стиль/импорты/баги
python -m mypy docker/core/admin_core --ignore-missing-imports

# тесты (нужен запущенный postgres из docker/):
cd docker && docker compose run --rm cli python -m pytest -q   # 115 тестов
```

CI — `.github/workflows/ci.yml`, на каждый push/PR: **lint** (ruff + mypy + compileall) →
**тесты** (pytest с сервисом postgres 16 и накатом init-схемы 001–004) → **документы**
(сборка 13 MD → HTML + валидация + проверка совпадения сгенерированного HTML с репозиторием).

Мониторинг ТГ-бота: `GET :8080/health` (JSON, 503 при недоступной БД) и `GET :8080/metrics`
(Prometheus-формат) — модуль `docker/core/admin_core/health.py`; rate-limit сообщений —
`docker/core/admin_core/ratelimit.py`.

Веб-панель (фаза 4, MVP): `docker compose up -d web` → http://localhost:8082/today,
вход по e-mail и паролю (`panel_users`, PBKDF2), при необходимости — код TOTP;
`/health` и `/metrics` — на 8083. Разделы: Сегодня, Записи, Клиенты, Прайс, Мастера,
Диалоги, Отчёты, Аудит, Настройки. **Правки работают**: добавление услуг, мастеров и
клиентов, архивация услуг и мастеров, цены и длительность, рабочие дни мастера, статус
записи, предоплата и заметки клиента — каждая правка в `audit_log` с указанием роли и
логина. Раздел «Отчёты» показывает выручку по дням, воронку бота, средний чек, загрузку
мастеров и потери за 30 дней. Роли: владелец точки, администратор, наша команда.

**Защита**: HTTPS + HSTS (год), CSP, nosniff, X-Frame-Options, no-store; cookie с
флагом `Secure` под HTTPS; `WEB_TRUST_PROXY=1` — только если панель за обратным
прокси. Локальный сертификат для проверки TLS: `powershell -File docker/scripts/make_dev_cert.ps1`.

**Сотрудники точки** создаются из панели (владельцем) или из CLI:
```
docker compose run --rm cli python cli.py --user boss@example.com --role owner \
    --password ПАРОЛЬ_8+ --salon "Виктория" [--totp]
```
Пароль и TOTP-секрет показываются один раз. Роль `team` создаётся только из CLI.
