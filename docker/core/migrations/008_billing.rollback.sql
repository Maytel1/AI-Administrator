-- Откат 008: платежи и срок оплаты. Удаляет историю оплат — только тестовая БД.
ALTER TABLE salons DROP COLUMN IF EXISTS paid_until;
DROP INDEX IF EXISTS idx_payments_salon_period;
DROP TABLE IF EXISTS payments;