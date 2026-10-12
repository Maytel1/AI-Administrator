-- 010: эквайринг в платежах (ЮKassa и совместимые провайдеры).
--
-- Ручной контур (перевод по реквизитам + подтверждение менеджером) остаётся
-- как есть: счёт живёт в той же таблице payments. Добавляем поля только для
-- онлайн-оплаты, чтобы старые счета и код биллинга не сломались. NULL в
-- provider* = «этот счёт оплачен вручную», поэтому миграция безопасна для
-- уже выставленных счетов.

ALTER TABLE payments
    ADD COLUMN IF NOT EXISTS provider             text,
    ADD COLUMN IF NOT EXISTS provider_payment_id  text,  -- id платежа у провайдера
    ADD COLUMN IF NOT EXISTS confirmation_url     text,  -- ссылка на оплату для салона
    ADD COLUMN IF NOT EXISTS idempotence_key      text;  -- защита от дублей при создании

-- Повторное уведомление провайдера о том же платеже не должно создавать вторую
-- строку: сверяемся по provider_payment_id в рамках одного салона.
CREATE UNIQUE INDEX IF NOT EXISTS ux_payments_provider_payment
    ON payments (salon_id, provider_payment_id)
    WHERE provider_payment_id IS NOT NULL;
