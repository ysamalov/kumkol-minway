-- =============================================================
-- Миграция 002: Справочник техники (vehicle_registry)
-- =============================================================
-- Решает проблему хрупкого substring-матча по полю nm.
-- Таблица заполняется автоматически при появлении новых
-- записей в снапшотах Wialon (через триггер).
-- Диспетчер может вручную скорректировать vehicle_type.
-- =============================================================

BEGIN;

-- =============================================================
-- 1. СПРАВОЧНИК ТИПОВ ТЕХНИКИ
-- =============================================================

CREATE TABLE IF NOT EXISTS public.vehicle_type_catalog (
    vehicle_type   VARCHAR(50)  PRIMARY KEY,
    description    TEXT,
    avg_speed_kmh  NUMERIC(5,1) NOT NULL DEFAULT 40.0,
    can_work_night BOOLEAN      NOT NULL DEFAULT TRUE
);

COMMENT ON TABLE  public.vehicle_type_catalog              IS 'Справочник типов спецтехники';
COMMENT ON COLUMN public.vehicle_type_catalog.vehicle_type IS 'Нормализованный тип: АЦН, ЦА-320, ЦА-400, АР32, БА60, УНБ-600 и т.д.';
COMMENT ON COLUMN public.vehicle_type_catalog.avg_speed_kmh IS 'Средняя скорость по умолчанию для данного типа техники';


-- =============================================================
-- 2. РЕЕСТР ТЕХНИКИ
-- =============================================================
-- Ключ: wialon_id (уникальный ID единицы в Wialon).
-- vehicle_type — нормализованный тип, извлечённый из nm или
--               проставленный диспетчером вручную.

CREATE TABLE IF NOT EXISTS public.vehicle_registry (
    wialon_id         BIGINT       PRIMARY KEY,
    registration_plate VARCHAR(50) NOT NULL,        -- из snapshot.registration_plate
    vehicle_type      VARCHAR(50)  NOT NULL
                          REFERENCES public.vehicle_type_catalog(vehicle_type)
                          DEFAULT 'UNKNOWN',
    display_name      TEXT,                         -- nm из последнего снапшота
    type_source       VARCHAR(20)  NOT NULL
                          DEFAULT 'auto'
                          CHECK (type_source IN ('auto', 'manual')),
                      -- 'auto'   = определено автоматически из nm
                      -- 'manual' = исправлено диспетчером вручную
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  public.vehicle_registry                    IS 'Реестр единиц техники с нормализованным типом';
COMMENT ON COLUMN public.vehicle_registry.wialon_id          IS 'ID единицы в Wialon — первичный ключ';
COMMENT ON COLUMN public.vehicle_registry.registration_plate IS 'Госномер из поля registration_plate снапшота';
COMMENT ON COLUMN public.vehicle_registry.vehicle_type       IS 'Нормализованный тип техники → vehicle_type_catalog';
COMMENT ON COLUMN public.vehicle_registry.type_source        IS 'auto = из nm, manual = скорректировано диспетчером';

CREATE INDEX IF NOT EXISTS idx_vehicle_registry_type
    ON public.vehicle_registry (vehicle_type);
CREATE INDEX IF NOT EXISTS idx_vehicle_registry_plate
    ON public.vehicle_registry (registration_plate);


-- =============================================================
-- 3. ФУНКЦИЯ: определение типа техники из строки nm
-- =============================================================
-- Правила применяются по порядку (первое совпадение побеждает).
-- Логика: сначала ищем специфичные паттерны (ЦА-400, УНБ-600),
-- потом общие (ЦА, АЦН).

-- =============================================================
-- 4. ФУНКЦИЯ-ТРИГГЕР: авто-заполнение реестра из снапшотов
-- =============================================================
-- Срабатывает при INSERT/UPDATE в любой из трёх таблиц снапшотов.
-- Логика UPSERT:
--   - Если записи нет → создаёт с type_source='auto'
--   - Если запись есть с type_source='auto' → обновляет display_name
--     и перепроверяет тип (вдруг nm исправили в Wialon)
--   - Если type_source='manual' → НЕ трогает vehicle_type
--     (диспетчер уже исправил вручную — не перезатираем)

CREATE OR REPLACE FUNCTION public.sync_vehicle_registry()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    detected_type VARCHAR(50);
BEGIN
    detected_type := public.detect_vehicle_type(NEW.nm);

    INSERT INTO public.vehicle_registry
        (wialon_id, registration_plate, vehicle_type, display_name, type_source)
    VALUES
        (NEW.wialon_id,
         COALESCE(NEW.registration_plate, ''),
         detected_type,
         NEW.nm,
         'auto')
    ON CONFLICT (wialon_id) DO UPDATE SET
        -- Госномер и отображаемое имя обновляем всегда
        registration_plate = EXCLUDED.registration_plate,
        display_name       = EXCLUDED.display_name,
        updated_at         = NOW(),
        -- Тип обновляем ТОЛЬКО если он был определён автоматически
        vehicle_type = CASE
            WHEN vehicle_registry.type_source = 'manual'
            THEN vehicle_registry.vehicle_type   -- диспетчер исправил → не трогаем
            ELSE EXCLUDED.vehicle_type           -- auto → обновляем
        END;

    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION public.sync_vehicle_registry IS
    'Триггер: при появлении новой записи в снапшоте Wialon автоматически '
    'добавляет или обновляет запись в vehicle_registry. '
    'Не перезаписывает vehicle_type если тип был исправлен вручную (type_source=manual).';


-- =============================================================
-- 5. ПРИВЯЗКА ТРИГГЕРОВ К СНАПШОТАМ
-- =============================================================

DROP TRIGGER IF EXISTS trg_sync_registry_snap1 ON "references".wialon_units_snapshot_1;
CREATE TRIGGER trg_sync_registry_snap1
    AFTER INSERT OR UPDATE ON "references".wialon_units_snapshot_1
    FOR EACH ROW EXECUTE FUNCTION public.sync_vehicle_registry();

DROP TRIGGER IF EXISTS trg_sync_registry_snap2 ON "references".wialon_units_snapshot_2;
CREATE TRIGGER trg_sync_registry_snap2
    AFTER INSERT OR UPDATE ON "references".wialon_units_snapshot_2
    FOR EACH ROW EXECUTE FUNCTION public.sync_vehicle_registry();

DROP TRIGGER IF EXISTS trg_sync_registry_snap3 ON "references".wialon_units_snapshot_3;
CREATE TRIGGER trg_sync_registry_snap3
    AFTER INSERT OR UPDATE ON "references".wialon_units_snapshot_3
    FOR EACH ROW EXECUTE FUNCTION public.sync_vehicle_registry();


-- =============================================================
-- 6. ПЕРВИЧНОЕ ЗАПОЛНЕНИЕ из уже существующих снапшотов
-- =============================================================
-- Выполняется один раз при накате миграции.
-- Берём самую свежую запись по каждому wialon_id.

INSERT INTO public.vehicle_registry
    (wialon_id, registration_plate, vehicle_type, display_name, type_source)
SELECT
    wialon_id,
    COALESCE(registration_plate, ''),
    public.detect_vehicle_type(nm),
    nm,
    'auto'
FROM (
    SELECT wialon_id, nm, registration_plate,
           ROW_NUMBER() OVER (PARTITION BY wialon_id ORDER BY pos_t DESC) AS rn
    FROM (
        SELECT wialon_id, nm, registration_plate, pos_t FROM "references".wialon_units_snapshot_1
        UNION ALL
        SELECT wialon_id, nm, registration_plate, pos_t FROM "references".wialon_units_snapshot_2
        UNION ALL
        SELECT wialon_id, nm, registration_plate, pos_t FROM "references".wialon_units_snapshot_3
    ) all_snaps
) ranked
WHERE rn = 1
ON CONFLICT (wialon_id) DO NOTHING;


-- =============================================================
-- 7. АВТООБНОВЛЕНИЕ updated_at для vehicle_registry
-- =============================================================

DROP TRIGGER IF EXISTS trg_vehicle_registry_updated_at ON public.vehicle_registry;
CREATE TRIGGER trg_vehicle_registry_updated_at
    BEFORE UPDATE ON public.vehicle_registry
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


COMMIT;
