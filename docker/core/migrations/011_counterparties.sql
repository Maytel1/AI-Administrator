-- 011: реестр контрагентов (подключённые клиенты) — общий на всю команду.
--
-- Зачем: счёт команды, привязанный к одной точке, не масштабируется на несколько
-- клиентов. Вместо карточки плательщика «на салон» — отдельный справочник,
-- который команда заполняет вручную. Его данные питают документы (счёт/акт)
-- и списки выбора: в документах участвуют только контрагенты отсюда.
--
-- Салон_id нет намеренно: контрагент — глобальная сущность команды, а не
-- tenant. Тип поля повторяет client_legal: набор реквизитов зависит от типа
-- (самозанятый / ИП / ООО), поэтому поля — в jsonb, а таблица одна.
CREATE TABLE IF NOT EXISTS counterparties (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title         text NOT NULL,              -- как показываем в списках (ФИО или наименование)
    client_type   text NOT NULL
                  CHECK (client_type IN ('self_employed','ip','llc')),
    legal_name    text NOT NULL,              -- ФИО или наименование для документа
    inn           text NOT NULL,
    details       jsonb NOT NULL DEFAULT '{}'::jsonb,
    contract_no   text,
    is_active     boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- Списки выбора и поиск идут по активным контрагентам по алфавиту.
CREATE INDEX IF NOT EXISTS idx_counterparties_active_title
    ON counterparties (lower(title)) WHERE is_active;
