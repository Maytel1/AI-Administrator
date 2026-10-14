# План: стенд для первого клиента на домашнем ПК

> Статус: план на 14.10.2026. Дополняет `PILOT_ONBOARDING_CHECKLIST.md`
> (там — что делать *для клиента*; здесь — *где и как это развернуть*).
> Путь выбран: **локально на этом компьютере** (план B — VPS, в конце).

## Текущее состояние (факты, проверено 14.10)

- Стек `ai-admin` работает полностью на этом ПК (Docker Desktop, Windows).
- `nginx` работает, но **является «сиротой»**: контейнер запущен вручную,
  в `docker-compose.yml` его больше нет — при пересоздании стека он не поднимется.
  Все его bind-маунты известны (см. этап 1.1).
- TLS — **dev-сертификат** (`certs/dev-cert.pem`, `WEB_PUBLIC_HOST=localhost`);
  в `docker/certs/` лежат `fullchain.pem`/`privkey.pem`, ACME-webroot
  (`docker/nginx/acme` → `/var/www/certbot`) и `location /.well-known/acme-challenge/`
  в nginx **уже настроены** — Let's Encrypt подключается почти без правок конфига.
- Все сервисы опубликованы на `0.0.0.0` (postgres 5433, панель 8082,
  prometheus 9090, bot 8080, whatsapp 8086 …) — наружу должны светиться
  только 80/443.
- YooKassa **выключен**: ключей в `.env` нет (работает ручной поток оплаты).
- Путь webhook ЮKassa в коде: `POST /pay/notify` (публичный, всегда 200),
  платёж перечитывается у провайдера — доверять телу webhook нельзя.
- Бот обслуживает **только старейший салон** (`ORDER BY created_at LIMIT 1`) —
  клиенту нужен отдельный стенд со своим токеном бота.
- Бэкапы (`Dezhurny24-Backup`), мониторинг в Telegram (`TG_ALERT_*`),
  `verify.py --fast`, смоуки — работают.

---

## Этап 1 — Гигиена экспозиции (≈2 часа)

**1.1 Вернуть nginx в `docker-compose.yml`.**
Сервис `nginx` (сейчас сирота `ai-admin-nginx`, образ `nginx:1.27-alpine`,
сеть `ai-admin_default`) с маунтами:

| На хосте | В контейнере |
|---|---|
| `docker/certs` | `/etc/nginx/certs` (ro) |
| `docker/nginx/nginx.conf` | `/etc/nginx/conf.d/default.conf` (ro) |
| `docker/nginx/proxy_params_default` | `/etc/nginx/proxy_params_default` (ro) |
| `docker/nginx/acme` | `/var/www/certbot` (ro) |
| `docker/nginx/options-ssl-params.conf` | `/etc/nginx/snippets/options-ssl-params.conf` (ro) |

Порты: `80:80`, `443:443`. Затем `docker compose up -d` (варнинг про «orphan» исчезнет).

**1.2 Закрыть сервисные порты на `127.0.0.1`.**
В `docker-compose.yml` все публикации перевести на формат
`"127.0.0.1:${VAR:-default}:…"`: postgres, web (+health), bot, whatsapp,
call, worker, prometheus, alertmanager, alert-relay. Наружу — только nginx.
(Защита на случай, если проброс на роутере когда-нибудь захватит лишнее.)

**1.3 Проверка:** `python verify.py --fast` + смоук панели → всё зелёное.

## Этап 2 — Домен и публичный адрес (1–3 часа)

**2.1 Домен** (пример: `salon.example.ru`) у любого регистратора.

**2.2 Выбор способа доступа — рекомендую Cloudflare Tunnel:**

- **Вариант А (рекомендуется): Cloudflare Tunnel.**
  Не надо открывать порты роутера и светить домашний IP, домашний динамический
  IP вообще не важен; есть HTTPS, WAF и DDoS-защита. Сервис `cloudflared`
  добавляется в compose (сеть общая с nginx), туннел:
  `hostname: salon.example.ru → http://nginx`, DNS-запись CNAME туннел создаст сам.
  Клиент → Edge Cloudflare → туннел → nginx. Плюс: этап 3 становится
  необязательным — TLS терминируется на Edge (Let's Encrypt автоматически).
- **Вариант Б: классический проброс 80/443 у роутера + DDNS.**
  ⚠️ У ряда домашних тарифов РФ-провайдеров порты 80/443 закрыты — проверить
  **до** покупки домена. Нужен DDNS (динамический IP) и файрвол Windows
  разрешает 80/443 только для Docker.

**2.3 Проверка:** `https://<домен>` отвечает панелью, HTTP→HTTPS редирект есть.

## Этап 3 — Настоящий TLS (≈1 час; при туннеле — опционально)

Если выбран проброс (или нужен валидный сертификат для прямого доступа):

1. Certbot в Docker по HTTP-01 (ACME-webroot уже смонтирован):

   ```powershell
   docker run --rm -v ${PWD}/nginx/acme:/var/www/certbot `
     -v ${PWD}/certs:/certs `
     certbot/certbot certonly --webroot -w /var/www/certbot `
     -d <домен> --agree-tos -m <email> --non-interactive
   # положить chain/privkey в docker/certs/fullchain.pem и privkey.pem
   ```

2. Автопродление: ежесуточная задача Task Scheduler (как бэкап) —
   `certbot renew` + `docker exec ai-admin-nginx nginx -s reload`.
3. Проверка: `docker/scripts/check_prod_tls.ps1` — issuer = Let's Encrypt,
   даты валидны.

При Cloudflare Tunnel: на Edge сертификат уже настоящий; внутренний nginx
может оставаться на self-signed (снаружи он не виден). `check_prod_tls.ps1`
гронять по `https://<домен>`.

## Этап 4 — YooKassa: боевая оплата (1–2 часа + проверка)

1. Регистрация в ЮKassa (ИП/ООО, договор эквайринга) → `ShopID`,
   секретный ключ.
2. `docker/.env` (имена ключей фактические, из `acquiring.py`):

   ```env
   YOOKASSA_SHOP_ID=...
   YOOKASSA_SECRET_KEY=...
   YOOKASSA_RETURN_URL=https://<домен>/pay   # возврат клиента после оплаты
   # YOOKASSA_BASE_URL=https://api.yookassa.prod  # боевой; по умолчанию api.yookassa.ru (тестовый)
   ```

3. Webhook в ЛК ЮKassa: `https://<домен>/pay/notify`. Фиксированного пути
   в ЛК может не быть — он создаётся при первом платеже либо добавляется
   вручную; код в любом случае **перечитывает платёж у провайдера** и сверяет
   сумму — подделка тела webhook невозможна.
4. Тест: `/pay` → кнопка карты → редирект → тестовая карта `4111 1111 1111 1111`
   → возврат → счёт в `payments` получает `provider_payment_id`, `paid_until`
   продвинулся, акт выдан (живая проверка ручного потока была 13.10).
5. Бой: боевые ключи в `.env`, мелкий реальный платёж, сверка выписки в ЛК.
6. `docker compose up -d --force-recreate web` (env подхватывается при
   пересоздании контейнера).

## Этап 5 — Отдельный стенд клиенту (4–6 часов; ядро плана)

**Зачем:** бот «привязан» к старейшему салону, dev-стенд с демо-данными и
учётками разработчика — не место для клиента. Клиенту: своя БД, свой токен
бота, свой набор контейнеров.

**Как (второй compose-проект `ai-client`):**

1. Каталог `docker/client/`: свой `docker-compose.yml` с `name: ai-client`,
   **своими** `container_name: ai-client-*` (у dev-стенда имена захардкожены —
   без этого второй стек не поднимется) и подключением к сети
   `ai-admin_default` как external (для nginx, см. п.5).
2. Порты: все уже параметризованы `${VAR:-default}` — в `client.env`
   задать сдвинутые значения: `POSTGRES_PORT=5533`, `WEB_HTTP_PORT=8182`,
   `BOT_HTTP_PORT=8180`, `CALL_HTTP_PORT=8181`, whatsapp `8186→8187`,
   health-порта +100 и т.д. Volume данных БД будет свой автоматически
   (имена volume'ов префиксуются именем проекта).
3. Токен бота: **новый** бот от `@BotFather` под клиентский салон
   (в `client.env`), текущий токен уже привязан к dev-салону.
4. Чистая БД → миграции → салон клиента (B1–B3 чек-листа) → учётка owner
   (C1) + 2FA (C3) → демо-наполнение не нужно (салон клиента).
5. nginx: пилотный домен → клиентскому вебу. Воспроизводимо:
   в сервисе nginx объявить вторую сеть внешней
   (`networks: { ai-admin_default: {}, ai-client_default: { external: true } }`),
   в `nginx.conf` добавить upstream и сервер:

   ```nginx
   upstream panel_client { server ai-client-web:8080; keepalive 16; }
   server { listen 443 ssl; server_name <домен клиента>;
            ssl_certificate /etc/nginx/certs/fullchain.pem;
            ssl_certificate_key /etc/nginx/certs/privkey.pem;
            include /etc/nginx/snippets/options-ssl-params.conf;
            location / { proxy_pass http://panel_client;
                         include /etc/nginx/proxy_params_default; } }
   ```

   Существующий `server_name _` остаётся dev-панелью; наружу по нему
   никто не ходит (домен клиента указывает именно на свой server-блок).
6. Бэкапы: **добавить вторую БД** в `docker/scripts/backup_db.ps1`
   (сейчас дампит одну) и в задачу `Dezhurny24-Backup`; разово прогнать
   `scripts/restore_check.py`.

**Альтернатива (не рекомендуется):** один «боевой» стенд вместо dev —
быстрее, но демо-данные, тестовые учётки и клиент смешиваются в одной БД.

## Этап 6 — Настройка под клиента (по чек-листу)

Пройти `PILOT_ONBOARDING_CHECKLIST.md` отдельно **для клиентского стенда**:
A1 (юр.минимум до старта), B (салон/наполнение), C (owner, 2FA, сотрудники),
D (юр.карточка, счёт/акт), E (каналы — токен бота клиента),
F (документы клиенту), G (финальная проверка).

## Этап 7 — Финальная приёмка перед доступом клиента (≈1 час)

- [ ] `python verify.py --fast` — 6/6.
- [ ] Смоук панели по `https://<домен>` (не по localhost).
- [ ] Тестовая → боевая оплата ЮKassa, акт сформирован.
- [ ] `restore_check.py` на свежем дампе **обеих** БД (имитация восстановления).
- [ ] Бот клиента отвечает; WhatsApp/звонок — если в пилоте.
- [ ] `check_prod_tls.ps1` — настоящий сертификат (если без туннеля).
- [ ] Диск с БД: BitLocker включён (152-ФЗ: место обработки защищено).

## Эксплуатация

- **ПК не выключать**: Docker Desktop → «Start Docker Desktop when you sign in»;
  сон отключить: `powercfg /change standby-timeout-ac 0`.
- **Бэкапы**: ежесуточно `LastTaskResult = 0`, `restore_check.py` — раз в месяц.
- **Мониторинг**: `alert-relay` уже шлёт алерты в Telegram (`TG_ALERT_*`) —
  проверить, что сообщение об остановке стека доходит.
- **Обновления**: `git pull → docker compose build → up -d → verify.py --fast
  → смоук`. Полный `pytest` — только после свежего бэкапа (баг из камня №8
  исправлен, но привычка пусть останется).

## Риски и план B

| Риск | Что делать |
|---|---|
| Нет аптайма (свет/интернет) | План B: перенос на VPS в РФ — тот же `docker compose`, вечер работы; данные — `pg_dump`/`restore_check` |
| Динамический IP | Cloudflare Tunnel (не важен вовсе) или DDNS |
| Провайдер блокирует 80/443 | Туннель; либо VPS |
| ПК «для себя» и для клиента одновременно | Клиентский стенд изолирован (этап 5); dev-работы — только в dev-стенде |

## Оценка времени

| Этап | Время |
|---|---|
| 1 — гигиена портов + nginx в compose | ≈2 ч |
| 2 — домен + туннель | 1–3 ч |
| 3 — TLS | ≈1 ч (при туннеле — 0) |
| 4 — YooKassa | 1–2 ч + проверочный платёж |
| 5 — стенд клиенту | 4–6 ч |
| 6–7 — настройка и приёмка | по чек-листу, зависит от клиента |

**Итого технической работы: 2–3 вечера.** Этапы 1–4 можно сделать заранее,
этап 5 — когда клиент определён, этапы 6–7 — по договору.
