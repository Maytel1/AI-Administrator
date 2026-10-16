-- Откат 011: реестр контрагентов. Откатывать стоит только на тестовой БД —
-- вместе со справочником теряются реквизиты, по которым выставлялись документы.
DROP INDEX IF EXISTS idx_counterparties_active_title;
DROP TABLE IF EXISTS counterparties;
