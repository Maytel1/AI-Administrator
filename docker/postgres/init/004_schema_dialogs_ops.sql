-- AI-администратор: схема БД фазы 1, часть 3/3 — диалоги, аудит, outbox.
-- Журнал аудита — LIABILITY_OPTIONS.md (споры решаются логом);
-- outbox — FAILOVER_SCENARIOS.md сценарий 4 (CRM недоступна → очередь).
-- Миграция идемпотентна. Требует: 002_schema_reference.sql.

BEGIN;

-- ═══ 6. ДИАЛОГИ ═══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS dialogs (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    salon_id    uuid NOT NULL REFERENCES salons(id) ON DELETE CASCADE,
    client_id   uuid REFERENCES clients(id) ON DELETE SET NULL,
    channel     text NOT NULL CHECK (channel IN ('voice','telegram','whatsapp','vk','cli')),
    started_at  timestamptz NOT NULL DEFAULT now(),
    ended_at    timestamptz,
    outcome     text,                              -- booked | rescheduled | faq | escalated | ...
    audio_url   text,                              -- YC Object Storage (фаза 3, lifecycle 90 дней)
    transcript  text,                              -- транскрипт хранится в РФ (PDN_LLM_OPTIONS.md)
    llm_cost    numeric(10,4),                     -- контроль себестоимости диалога
    llm_model   text
);
CREATE INDEX IF NOT EXISTS idx_dialogs_salon_time ON dialogs(salon_id, started_at DESC);

CREATE TABLE IF NOT EXISTS dialog_messages (
    id         bigserial PRIMARY KEY,
    dialog_id  uuid NOT NULL REFERENCES dialogs(id) ON DELETE CASCADE,
    role       text NOT NULL CHECK (role IN ('system','user','assistant','tool')),
    content    text NOT NULL,                      -- ПДн в РФ; в LLM уходит обезличенно
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_dialog_messages_dialog ON dialog_messages(dialog_id);

-- ═══ 7. ЖУРНАЛ АУДИТА (LIABILITY_OPTIONS.md) ══════════════════════════════
CREATE TABLE IF NOT EXISTS audit_log (
    id         bigserial PRIMARY KEY,
    ts         timestamptz NOT NULL DEFAULT now(),
    salon_id   uuid,
    actor      text NOT NULL,                      -- 'bot' | 'owner:lk' | 'admin:web' | 'system'
    action     text NOT NULL,                      -- 'create_booking', 'cancel_booking', ...
    details    jsonb
);
CREATE INDEX IF NOT EXISTS idx_audit_salon_time ON audit_log(salon_id, ts DESC);

-- ═══ 8. OUTBOX (FAILOVER_SCENARIOS.md) ════════════════════════════════════
CREATE TABLE IF NOT EXISTS outbox (
    id           bigserial PRIMARY KEY,
    kind         text NOT NULL,                    -- 'crm_create_booking' | 'sms_confirm' | ...
    payload      jsonb NOT NULL,
    status       text NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','processing','done','failed')),
    attempts     integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 5,
    run_at       timestamptz NOT NULL DEFAULT now(),
    last_error   text,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_outbox_due ON outbox(run_at) WHERE status = 'pending';

COMMIT;
