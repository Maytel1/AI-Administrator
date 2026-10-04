-- 008: ручной счёт + СБП по реквизитам, помесячные тарифы.
--
-- Клиент платит переводом по реквизитам (СБП), карты не храним, поэтому
-- автосписания нет: платёж отмечает менеджер. Без таблицы платежей тариф был
-- просто буквой в поле plan, а «оплачено» означало «мы решили, что оплачено».
CREATE TABLE IF NOT EXISTS payments (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    salon_id     uuid NOT NULL REFERENCES salons(id) ON DELETE CASCADE,
    plan         text NOT NULL,
    period_start date NOT NULL,
    period_end   date NOT NULL,             -- помесячный период: [start, end)
    amount       numeric(12,2) NOT NULL CHECK (amount >= 0),
    method       text NOT NULL DEFAULT 'sbp'
                 CHECK (method IN ('sbp','bank_transfer','cash')),
    reference    text,                      -- назначение платежа для банка
    invoice_no   text,                      -- номер счёта
    status       text NOT NULL DEFAULT 'issued'
                 CHECK (status IN ('issued','paid','overdue','void')),
    issued_at    timestamptz NOT NULL DEFAULT now(),
    paid_at      timestamptz,
    note         text,
    created_by   text,                      -- кто выставил/отметил: owner:login
    UNIQUE (salon_id, period_start)
);

CREATE INDEX IF NOT EXISTS idx_payments_salon_period
    ON payments (salon_id, period_start DESC);

-- Срок оплаченного периода тарифа. NULL = тариф не оплачен (или триал).
ALTER TABLE salons ADD COLUMN IF NOT EXISTS paid_until date;
