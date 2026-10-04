-- Откат 007: журнал входящих вебхуков. Входящие сообщения — история, поэтому
-- откатывать стоит только на тестовой БД.
DROP INDEX IF EXISTS idx_inbound_salon_time;
DROP TABLE IF EXISTS inbound_messages;
