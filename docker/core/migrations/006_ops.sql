-- 006: эксплуатационные таблицы — напоминания, лист ожидания, лог входов,
-- алерты и сессии панели. Всё, что нужно боту, worker'у и безопасности.

-- 1. ДИАЛОГИ: канал и внешний идентификатор чата --------------------------
-- Код бота и worker'а пишут chat_id (id чата в Telegram), а в исходной схеме
-- его не было: диалог создавался, но найти его по чату было нельзя.
ALTER TABLE dialogs ADD COLUMN IF NOT EXISTS chat_id text;
CREATE INDEX IF NOT EXISTS idx_dialogs_chat ON dialogs (chat_id) WHERE chat_id IS NOT NULL;

-- 2. НАПОМИНАНИЯ ------------------------------------------------------------
-- Клиенту пишем за сутки и за два часа до визита. Одна и та же запись не должна
-- получать одно и то же напоминание дважды даже при перезапуске worker'а:
-- уникальный ключ (booking_id, kind) делает повторную отправку невозможной.
CREATE TABLE IF NOT EXISTS reminders (
    id          bigserial PRIMARY KEY,
    booking_id  uuid NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
    kind        text NOT NULL,                    -- day | hours | waitlist
    status      text NOT NULL DEFAULT 'sent'
                CHECK (status IN ('sent','skipped','failed')),
    sent_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (booking_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_reminders_booking ON reminders (booking_id);

-- 3. ЛИСТ ОЖИДАНИЯ ---------------------------------------------------------
-- Слот освободился — отдаём его тому, кто ждёт. active=false после предложения,
-- чтобы один клиент не получал предложение на каждое освобождение подряд.
CREATE TABLE IF NOT EXISTS waitlist (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    salon_id     uuid NOT NULL REFERENCES salons(id) ON DELETE CASCADE,
    client_id    uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    service_id   uuid REFERENCES services(id) ON DELETE SET NULL,
    note         text,
    active       boolean NOT NULL DEFAULT true,
    offered_at   timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_waitlist_queue
    ON waitlist (salon_id, service_id, created_at) WHERE active;

-- 4. ЖУРНАЛ ВХОДОВ ---------------------------------------------------------
-- Раньше неудачные попытки жили только в памяти процесса и терялись при
-- рестарте. Для разбора инцидента нужен след: кто, откуда, когда и с чем.
CREATE TABLE IF NOT EXISTS login_log (
    id         bigserial PRIMARY KEY,
    ts         timestamptz NOT NULL DEFAULT now(),
    login      text,
    ip         text,
    ok         boolean NOT NULL DEFAULT false,
    reason     text,
    user_agent text
);
CREATE INDEX IF NOT EXISTS idx_login_log_ts ON login_log (ts DESC);
CREATE INDEX IF NOT EXISTS idx_login_log_fail ON login_log (ts DESC) WHERE NOT ok;

-- 5. АЛЕРТЫ ----------------------------------------------------------------
-- last_sent защищает от «звонит каждые 30 секунд»: повтор — не чаще раза в час.
CREATE TABLE IF NOT EXISTS alerts (
    kind        text PRIMARY KEY,
    text        text NOT NULL,
    first_seen  timestamptz NOT NULL DEFAULT now(),
    last_sent   timestamptz,
    times       integer NOT NULL DEFAULT 0
);

-- 6. СЕССИИ ПАНЕЛИ ---------------------------------------------------------
-- Общий стор: при нескольких инстансах веб-сервиса сессия не должна жить
-- в памяти одного процесса. CSRF-токен по-прежнему привязан к токену сессии.
CREATE TABLE IF NOT EXISTS panel_sessions (
    token      text PRIMARY KEY,
    user_id    uuid,
    login      text,
    role       text NOT NULL,
    salon_id   uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_panel_sessions_expiry ON panel_sessions (expires_at);