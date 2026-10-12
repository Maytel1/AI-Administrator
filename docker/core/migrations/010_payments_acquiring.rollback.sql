-- Откат 010: убираем поля эквайринга из payments.
-- Обратно несовместим с данными только в том смысле, что онлайн-счета потеряют
-- привязку к провайдеру; ручной контур (перевод по реквизитам) не затронут.

DROP INDEX IF EXISTS ux_payments_provider_payment;

ALTER TABLE payments
    DROP COLUMN IF EXISTS idempotence_key,
    DROP COLUMN IF EXISTS confirmation_url,
    DROP COLUMN IF EXISTS provider_payment_id,
    DROP COLUMN IF EXISTS provider;
