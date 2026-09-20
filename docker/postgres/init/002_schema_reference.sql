-- AI-администратор: схема БД фазы 1, часть 1/3 — справочники.
-- DEVELOPMENT_PLAN.md, «Фаза 1»: салоны, услуги, мастера (слоты = нарезка графика).
-- Мультиарендность: салон = tenant (фаза 4). Миграция идемпотентна.

BEGIN;

-- ═══ 1. САЛОНЫ (tenant) ═══════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS salons (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name            text NOT NULL,
    phone           text,
    timezone        text NOT NULL DEFAULT 'Asia/Yekaterinburg',  -- Тюмень (UTC+5)
    plan            text NOT NULL DEFAULT 'trial',               -- trial | start | standard | pro | network
    crm_provider    text NOT NULL DEFAULT 'none',                -- none | internal | altegio | yclients
    crm_ids         jsonb NOT NULL DEFAULT '{}'::jsonb,          -- ID салона в CRM; секреты — в Lockbox
    settings        jsonb NOT NULL DEFAULT '{}'::jsonb,          -- FAQ, тон, режим переадресации
    is_active       boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- ═══ 2. УСЛУГИ ════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS services (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    salon_id     uuid NOT NULL REFERENCES salons(id) ON DELETE CASCADE,
    external_id  text,                       -- ID услуги в Altegio/YCLIENTS
    title        text NOT NULL,
    duration_min integer NOT NULL CHECK (duration_min BETWEEN 5 AND 600),
    price        numeric(10,2),
    is_active    boolean NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (salon_id, title)
);
CREATE INDEX IF NOT EXISTS idx_services_salon ON services(salon_id) WHERE is_active;
CREATE INDEX IF NOT EXISTS idx_services_title_trgm ON services USING gin (title gin_trgm_ops);

-- ═══ 3. МАСТЕРА ═══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS masters (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    salon_id    uuid NOT NULL REFERENCES salons(id) ON DELETE CASCADE,
    external_id text,
    name        text NOT NULL,
    is_active   boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (salon_id, name)
);

CREATE TABLE IF NOT EXISTS master_services (          -- что умеет мастер
    master_id  uuid NOT NULL REFERENCES masters(id) ON DELETE CASCADE,
    service_id uuid NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    PRIMARY KEY (master_id, service_id)
);

CREATE TABLE IF NOT EXISTS working_hours (            -- график мастера; слоты = нарезка графика
    master_id  uuid NOT NULL REFERENCES masters(id) ON DELETE CASCADE,
    weekday    smallint NOT NULL CHECK (weekday BETWEEN 0 AND 6),  -- 0=Пн .. 6=Вс
    start_time time NOT NULL,
    end_time   time NOT NULL,
    CHECK (start_time < end_time),
    PRIMARY KEY (master_id, weekday)
);

COMMIT;
