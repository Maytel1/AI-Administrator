-- AI-администратор: стартовая инициализация БД (фаза 0/1).
-- Полная схема (салоны, услуги, мастера, слоты, клиенты, записи, диалоги) — фаза 1,
-- см. DEVELOPMENT_PLAN.md «Фаза 1 — „Мозг“ и CRM-слой».

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- uuid / хэши
CREATE EXTENSION IF NOT EXISTS pg_trgm;    -- быстрый поиск по названию услуг

-- В Managed PostgreSQL (прод, фаза 2+) подтверждены timescaledb и pgvector —
-- DEVELOPMENT_PLAN.md, раздел 1.3. Локально добавим, когда понадобятся.
