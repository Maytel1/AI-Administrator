-- AI-администратор: пресеты вертикалей (дефолтные услуги, роли, тарифы, модули, термины, тема).
-- Применяются при смене ``vertical`` в ``theme_change`` (actions.py).
-- Миграция идемпотентна.

BEGIN;

CREATE TABLE IF NOT EXISTS vertical_presets (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    vertical        text NOT NULL UNIQUE,
    label           text NOT NULL,
    default_plan    text NOT NULL DEFAULT 'start',
    services        jsonb NOT NULL DEFAULT '[]'::jsonb,
    roles           jsonb NOT NULL DEFAULT '[]'::jsonb,
    modules         jsonb NOT NULL DEFAULT '{}'::jsonb,
    terms           jsonb NOT NULL DEFAULT '{}'::jsonb,
    theme           jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- ═══ БЬЮТИ (салон красоты / барбершоп) ═══
INSERT INTO vertical_presets (vertical, label, default_plan, services, roles, modules, terms, theme)
VALUES (
    'beauty',
    'Салон красоты / барбершоп',
    'start',
    '[
        {"title": "Женская стрижка", "duration_min": 45, "price": 2500},
        {"title": "Мужская стрижка", "duration_min": 30, "price": 1500},
        {"title": "Окрашивание волос", "duration_min": 90, "price": 5000},
        {"title": "Маникюр", "duration_min": 60, "price": 2000},
        {"title": "Педикюр", "duration_min": 60, "price": 2500}
    ]'::jsonb,
    '[
        {"name": "Мастер универсал", "role": "staff"}
    ]'::jsonb,
    '{"clients": true, "services": true, "masters": true, "bookings": true, "dialogs": true, "reports": true, "pay": true, "settings": true}'::jsonb,
    '{"staff": "мастер", "staff_plural": "мастера", "client": "клиент", "service": "услуга", "appointment": "запись", "booking": "запись"}'::jsonb,
    '{"accent": "#c2410c"}'::jsonb
) ON CONFLICT (vertical) DO NOTHING;

-- ═══ АВТОСЕРВИС / ШИНОМОНТАЖ ═══
INSERT INTO vertical_presets (vertical, label, default_plan, services, roles, modules, terms, theme)
VALUES (
    'auto',
    'Автосервис / шиномонтаж',
    'start',
    '[
        {"title": "Замена масла", "duration_min": 45, "price": 1200},
        {"title": "Шиномонтаж (комплект)", "duration_min": 60, "price": 2500},
        {"title": "Диагностика двигателя", "duration_min": 30, "price": 1500},
        {"title": "ТО регламентное", "duration_min": 120, "price": 8000},
        {"title": "Замена тормозных колодок", "duration_min": 60, "price": 3000}
    ]'::jsonb,
    '[
        {"name": "Механик", "role": "staff"}
    ]'::jsonb,
    '{"clients": true, "services": true, "masters": true, "bookings": true, "dialogs": false, "reports": true, "pay": false, "settings": true}'::jsonb,
    '{"staff": "механик", "staff_plural": "механики", "client": "владелец авто", "service": "работы", "appointment": "заказ-наряд", "booking": "заказ-наряд"}'::jsonb,
    '{"accent": "#1f6feb"}'::jsonb
) ON CONFLICT (vertical) DO NOTHING;

-- ═══ СТОМАТОЛОГИЯ / КЛИНИКА ═══
INSERT INTO vertical_presets (vertical, label, default_plan, services, roles, modules, terms, theme)
VALUES (
    'dental',
    'Стоматология / клиника',
    'start',
    '[
        {"title": "Осмотр стоматолога", "duration_min": 30, "price": 1000},
        {"title": "Лечение кариеса", "duration_min": 60, "price": 4000},
        {"title": "Профессиональная гигиена", "duration_min": 45, "price": 3500},
        {"title": "Отбеливание зубов", "duration_min": 60, "price": 12000},
        {"title": "Установка пломбы", "duration_min": 45, "price": 3000}
    ]'::jsonb,
    '[
        {"name": "Врач-стоматолог", "role": "staff"}
    ]'::jsonb,
    '{"clients": true, "services": true, "masters": true, "bookings": true, "dialogs": false, "reports": true, "pay": false, "settings": true}'::jsonb,
    '{"staff": "врач", "staff_plural": "врачи", "client": "пациент", "service": "лечение", "appointment": "приём", "booking": "приём"}'::jsonb,
    '{"accent": "#2e7d5b"}'::jsonb
) ON CONFLICT (vertical) DO NOTHING;

-- ═══ КЛИНИНГ ═══
INSERT INTO vertical_presets (vertical, label, default_plan, services, roles, modules, terms, theme)
VALUES (
    'cleaning',
    'Клининг',
    'start',
    '[
        {"title": "Уборка квартиры после ремонта", "duration_min": 180, "price": 8000},
        {"title": "Поддерживающая уборка", "duration_min": 120, "price": 4000},
        {"title": "Химчистка ковра", "duration_min": 60, "price": 2500},
        {"title": "Мытьё окон", "duration_min": 90, "price": 3000},
        {"title": "Дезинфекция помещений", "duration_min": 60, "price": 3500}
    ]'::jsonb,
    '[
        {"name": "Клининг-менеджер", "role": "staff"}
    ]'::jsonb,
    '{"clients": true, "services": true, "masters": true, "bookings": true, "dialogs": false, "reports": true, "pay": false, "settings": true}'::jsonb,
    '{"staff": "менеджер", "staff_plural": "менеджеры", "client": "заказчик", "service": "услуга", "appointment": "заявка", "booking": "заявка"}'::jsonb,
    '{"accent": "#7c3aed"}'::jsonb
) ON CONFLICT (vertical) DO NOTHING;

COMMIT;