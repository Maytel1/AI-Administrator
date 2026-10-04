-- Откат 006: рабочие таблицы операций.
--
-- Лежит отдельным файлом, а не секцией в конце миграции: строки после
-- «-- ROLLBACK» — обычный SQL, и при накате они бы выполнились вместе с
-- миграцией и удалили только что созданные таблицы.
--
-- ВНИМАНИЕ: откат удаляет рабочие данные (напоминания, лист ожидания, сессии),
-- поэтому применять его стоит только на тестовой БД.
DROP TABLE IF EXISTS panel_sessions;
DROP TABLE IF EXISTS login_log;
DROP TABLE IF EXISTS alert_log;
DROP TABLE IF EXISTS waitlist;
DROP TABLE IF EXISTS reminders;
DROP INDEX IF EXISTS idx_dialogs_chat;
ALTER TABLE dialogs DROP COLUMN IF EXISTS chat_id;
