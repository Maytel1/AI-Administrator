-- Откат 009: карточка плательщика. Откатывать стоит только на тестовой БД —
-- вместе с карточкой теряются реквизиты, по которым выставлялись счета.
DROP INDEX IF EXISTS idx_bookings_salon_starts;
DROP INDEX IF EXISTS idx_payments_salon_status;
DROP INDEX IF EXISTS idx_payments_period_end;
DROP TABLE IF EXISTS client_legal;
