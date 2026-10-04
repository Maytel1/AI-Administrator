-- 007: идемпотентность входящих вебхуков (WhatsApp и будущие каналы).
--
-- Meta повторяет webhook при сбое доставки. Без учёта wamid агент отвечал бы
-- клиенту дважды и мог создать вторую бронь, поэтому каждый входящий id
-- фиксируется ДО обработки: повторный вызов увидит конфликт и уйдёт тихо.
CREATE TABLE IF NOT EXISTS inbound_messages (
    id           bigserial PRIMARY KEY,
    channel      text NOT NULL CHECK (channel IN ('whatsapp','telegram','vk','voice')),
    external_id  text NOT NULL,
    salon_id     uuid REFERENCES salons(id) ON DELETE CASCADE,
    client_id    uuid REFERENCES clients(id) ON DELETE SET NULL,
    preview      text,                     -- первые 120 символов: для разбора, не ПДн
    received_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (channel, external_id)
);

CREATE INDEX IF NOT EXISTS idx_inbound_salon_time
    ON inbound_messages (salon_id, received_at DESC);
