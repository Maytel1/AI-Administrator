-- AI-администратор: схема БД фазы 1, часть 2/3 — клиенты и записи.
-- Решения 20.09.2026: опознание «своих» по номеру телефона (вопрос 7),
-- флаг предоплаты на каждого клиента (вопрос 1), защита от двойных броней.
-- Миграция идемпотентна. Требует: 002_schema_reference.sql (salons, masters, services).

BEGIN;

-- ═══ 4. КЛИЕНТЫ ═══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS clients (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    salon_id             uuid NOT NULL REFERENCES salons(id) ON DELETE CASCADE,
    external_id          text,
    phone                text NOT NULL,
    name                 text,
    notes                text,                           -- пожелания/аллергии — в контекст диалога
    preferred_master_id  uuid REFERENCES masters(id) ON DELETE SET NULL,
    requires_prepayment  boolean NOT NULL DEFAULT false, -- вопрос 1: на каждого клиента отдельно
    created_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (salon_id, phone)                             -- опознание «своих» по номеру
);
CREATE INDEX IF NOT EXISTS idx_clients_salon_phone ON clients(salon_id, phone);

-- ═══ 5. ЗАПИСИ ════════════════════════════════════════════════════════════
CREATE EXTENSION IF NOT EXISTS btree_gist;             -- для exclusion-констрейнта

CREATE TABLE IF NOT EXISTS bookings (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    salon_id          uuid NOT NULL REFERENCES salons(id) ON DELETE CASCADE,
    client_id         uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    master_id         uuid NOT NULL REFERENCES masters(id) ON DELETE RESTRICT,
    service_id        uuid NOT NULL REFERENCES services(id) ON DELETE RESTRICT,
    starts_at         timestamptz NOT NULL,
    ends_at           timestamptz NOT NULL,
    status            text NOT NULL DEFAULT 'booked'
                      CHECK (status IN ('booked','confirmed','completed','cancelled','no_show')),
    prepayment_status text NOT NULL DEFAULT 'not_required'
                      CHECK (prepayment_status IN ('not_required','offered','paid','declined')),
    source            text NOT NULL DEFAULT 'bot'       -- bot | manual | crm_sync
                      CHECK (source IN ('bot','manual','crm_sync')),
    external_id       text,                             -- ID записи в CRM
    created_by        text,                             -- 'bot:tg' | 'bot:voice' | 'owner:lk'
    created_at        timestamptz NOT NULL DEFAULT now(),
    CHECK (starts_at < ends_at),
    -- ДВОЙНЫЕ БРОНИ ИСКЛЮЧЕНЫ НА УРОВНЕ БД: у мастера не пересекаются активные записи
    EXCLUDE USING gist (
        master_id WITH =,
        tstzrange(starts_at, ends_at) WITH &&
    ) WHERE (status IN ('booked','confirmed'))
);
CREATE INDEX IF NOT EXISTS idx_bookings_salon_time ON bookings(salon_id, starts_at);
CREATE INDEX IF NOT EXISTS idx_bookings_client     ON bookings(client_id); -- история визитов (вопрос 7)

COMMIT;
