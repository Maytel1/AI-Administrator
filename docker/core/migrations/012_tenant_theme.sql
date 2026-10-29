-- AI-администратор: разделение по вертикалям (гибрид Варианта 5).
-- Добавляет в salons колонки для мульти-направленности:
--   vertical      — направление бизнеса (beauty | auto | dental | cleaning | ...)
--   theme         — визуальная тема (акцентный цвет, логотип)
--   modules       — какие разделы панели включены для этого тенанта
--   terms         — словарь терминов (мастер/клиент/услуга и т.д.)
-- Миграция идемпотентна.

BEGIN;

ALTER TABLE salons
    ADD COLUMN IF NOT EXISTS vertical text NOT NULL DEFAULT 'beauty';

ALTER TABLE salons
    ADD COLUMN IF NOT EXISTS theme jsonb NOT NULL DEFAULT '{"accent": "#c2410c", "logo": null}'::jsonb;

ALTER TABLE salons
    ADD COLUMN IF NOT EXISTS modules jsonb NOT NULL DEFAULT '{"clients": true, "services": true, "masters": true, "bookings": true, "dialogs": true, "reports": true, "pay": true, "settings": true}'::jsonb;

ALTER TABLE salons
    ADD COLUMN IF NOT EXISTS terms jsonb NOT NULL DEFAULT '{"staff": "мастер", "staff_plural": "мастера", "client": "клиент", "service": "услуга", "appointment": "запись", "booking": "запись"}'::jsonb;

COMMIT;