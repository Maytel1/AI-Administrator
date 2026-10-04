-- 009: карточка плательщика (реквизиты) + дата автовыставления счёта.
--
-- Зачем: счёт по СБП физическому лицу не выставить — банк требует ИНН и
-- счёт. Тип плательщика меняет набор полей (самозанятый / ИП / ООО), поэтому
-- тип хранится явно, а поля — в jsonb: набор разный, а таблица одна.
CREATE TABLE IF NOT EXISTS client_legal (
    salon_id          uuid PRIMARY KEY REFERENCES salons(id) ON DELETE CASCADE,
    client_type       text NOT NULL
                     CHECK (client_type IN ('self_employed','ip','llc')),
    legal_name        text NOT NULL,          -- ФИО или наименование
    inn               text NOT NULL,
    details           jsonb NOT NULL DEFAULT '{}'::jsonb,
    invoice_due_day   smallint NOT NULL DEFAULT 1
                     CHECK (invoice_due_day BETWEEN 1 AND 28),
    auto_invoice      boolean NOT NULL DEFAULT true,
    contract_no       text,
    contract_date     date,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

-- Долги и сводки считаются по дате счёта: без индекса берём весь payments.
CREATE INDEX IF NOT EXISTS idx_payments_period_end
    ON payments (period_end DESC) WHERE status = 'issued';
CREATE INDEX IF NOT EXISTS idx_payments_salon_status
    ON payments (salon_id, status, period_start DESC);
-- Отчёты считают выручку по диапазону дат.
CREATE INDEX IF NOT EXISTS idx_bookings_salon_starts
    ON bookings (salon_id, starts_at DESC);
