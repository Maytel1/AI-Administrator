-- AI-администратор: пользователи панели (фаза 4, §8.2 — RBAC + TOTP 2FA).
--
-- Один общий пароль на всех заменяем учётными записями: у каждого своя роль,
-- свой салон (tenant_id) и включённый по умолчанию TOTP. Секрет TOTP и хеш
-- пароля хранятся только здесь, пароль в открытом виде не сохраняется нигде.
-- Миграция идемпотентна. Требует: 002_schema_reference.sql (salons).

-- Внешний BEGIN/COMMIT убран: run_migrations оборачивает каждый файл в свою
-- транзакцию, а свой COMMIT внутри рвал внешнюю транзакцию (test_migrations_rollback).

-- ═══ ПОЛЬЗОВАТЕЛИ ПАНЕЛИ ════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS panel_users (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    salon_id       uuid REFERENCES salons(id) ON DELETE CASCADE,
    login          text NOT NULL UNIQUE,          -- e-mail или логин точки
    name           text,
    role           text NOT NULL DEFAULT 'staff'
                   CHECK (role IN ('owner','staff','team')),
    -- pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex> — открытый пароль не хранится
    password_hash  text NOT NULL,
    -- base32-секрет TOTP (RFC 6238); NULL — 2FA выключена у пользователя
    totp_secret    text,
    totp_enabled   boolean NOT NULL DEFAULT false,
    is_active      boolean NOT NULL DEFAULT true,
    last_login_at  timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now()
);

-- Индекс под вход: логин уникален, ищем только активных
CREATE INDEX IF NOT EXISTS idx_panel_users_active ON panel_users (login) WHERE is_active;
-- Список команды салона (владелец управляет доступом своей точки)
CREATE INDEX IF NOT EXISTS idx_panel_users_salon ON panel_users (salon_id) WHERE is_active;

-- COMMIT выше не нужен — см. комментарий в начале файла.
