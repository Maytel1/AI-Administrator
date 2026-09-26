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
| Тарифы | [TARIFFS.html](TARIFFS.html) | [TARIFFS.md](TARIFFS.md) | ждёт выбора сетки |
| Ответственность | [LIABILITY_OPTIONS.html](LIABILITY_OPTIONS.html) | [LIABILITY_OPTIONS.md](LIABILITY_OPTIONS.md) | ждёт выбора варианта |
| Точка без CRM | [SALON_WITHOUT_CRM.html](SALON_WITHOUT_CRM.html) | [SALON_WITHOUT_CRM.md](SALON_WITHOUT_CRM.md) | ждёт выбора сценария |
| ПДн и LLM | [PDN_LLM_OPTIONS.html](PDN_LLM_OPTIONS.html) | [PDN_LLM_OPTIONS.md](PDN_LLM_OPTIONS.md) | ждёт выбора варианта |
| Договор ПДн | [CONTRACT_PDN_TEMPLATE.html](CONTRACT_PDN_TEMPLATE.html) | [CONTRACT_PDN_TEMPLATE.md](CONTRACT_PDN_TEMPLATE.md) | проверить у юриста |
| Сбои и failover | [FAILOVER_SCENARIOS.html](FAILOVER_SCENARIOS.html) | [FAILOVER_SCENARIOS.md](FAILOVER_SCENARIOS.md) | перед фазой 3 |
| Открытые вопросы | [OPEN_QUESTIONS.html](OPEN_QUESTIONS.html) | [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) | парк вопросов (29) |
| Аудит проекта | [GAP_ANALYSIS.html](GAP_ANALYSIS.html) | [GAP_ANALYSIS.md](GAP_ANALYSIS.md) | новый · 26.09.2026 |

## Перегенерация

```bash
python build_html_docs.py     # 13 документов md -> html + валидация разметки
python validate_plan_html.py  # паритет DEVELOPMENT_PLAN.html ↔ DEVELOPMENT_PLAN.md
```

Markdown — источник истины (`DEVELOPMENT_PLAN.html` генерируется из MD: раздел,
таблицы и чек-листы в HTML совпадают с MD). Нумерация секций берётся из MD, поэтому
ссылки вида «§4.4» в тексте указывают на тот же раздел и в HTML.
`index.html` и этот README обновляются вручную при добавлении новых документов.
