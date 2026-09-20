# AI-администратор для салонов красоты — документы проекта

Продукт: голосовой + текстовый AI-администратор (звонки 24/7, WhatsApp/Telegram/VK,
запись в YCLIENTS/Altegio) для салонов красоты и барбершопов Тюмени.

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
| План разработки | [DEVELOPMENT_PLAN.html](DEVELOPMENT_PLAN.html) | [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) | v1.2 |
| Тарифы | [TARIFFS.html](TARIFFS.html) | [TARIFFS.md](TARIFFS.md) | ждёт выбора сетки |
| Ответственность | [LIABILITY_OPTIONS.html](LIABILITY_OPTIONS.html) | [LIABILITY_OPTIONS.md](LIABILITY_OPTIONS.md) | ждёт выбора варианта |
| Салон без CRM | [SALON_WITHOUT_CRM.html](SALON_WITHOUT_CRM.html) | [SALON_WITHOUT_CRM.md](SALON_WITHOUT_CRM.md) | ждёт выбора сценария |
| ПДн и LLM | [PDN_LLM_OPTIONS.html](PDN_LLM_OPTIONS.html) | [PDN_LLM_OPTIONS.md](PDN_LLM_OPTIONS.md) | ждёт выбора варианта |
| Договор ПДн | [CONTRACT_PDN_TEMPLATE.html](CONTRACT_PDN_TEMPLATE.html) | [CONTRACT_PDN_TEMPLATE.md](CONTRACT_PDN_TEMPLATE.md) | проверить у юриста |
| Сбои и failover | [FAILOVER_SCENARIOS.html](FAILOVER_SCENARIOS.html) | [FAILOVER_SCENARIOS.md](FAILOVER_SCENARIOS.md) | перед фазой 3 |
| Открытые вопросы | [OPEN_QUESTIONS.html](OPEN_QUESTIONS.html) | [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) | парк вопросов |

## Перегенерация

```bash
python build_html_docs.py   # 9 документов md -> html + валидация
```

Markdown — источник истины. `index.html` и этот README обновляются вручную
при добавлении новых документов.
