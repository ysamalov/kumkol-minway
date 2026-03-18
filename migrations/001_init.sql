-- =============================================================
-- ИС УТО — Полная схема базы данных
-- =============================================================

BEGIN;

-- =============================================================
-- 0. СХЕМА
-- =============================================================
-- "references" — зарезервированное слово PostgreSQL, поэтому везде в кавычках

CREATE SCHEMA IF NOT EXISTS "references";

-- =============================================================
-- 1. ГРАФ ДОРОГ (схема references — данные организаторов)
-- =============================================================

CREATE TABLE IF NOT EXISTS "references".road_nodes (
    id       SERIAL        PRIMARY KEY,
    node_id  INTEGER       NOT NULL UNIQUE,   -- логический ID узла для построения графа
    lon      NUMERIC(12,8) NOT NULL,           -- долгота (обезличена, метрически согласована)
    lat      NUMERIC(12,8) NOT NULL            -- широта
);

COMMENT ON TABLE  "references".road_nodes         IS 'Узлы дорожного графа месторождения (из GPS-треков)';
COMMENT ON COLUMN "references".road_nodes.node_id IS 'Логический идентификатор узла для построения графа';
COMMENT ON COLUMN "references".road_nodes.lon     IS 'Долгота (обезличена, метрически согласована)';
COMMENT ON COLUMN "references".road_nodes.lat     IS 'Широта';

CREATE INDEX IF NOT EXISTS idx_road_nodes_coords
    ON "references".road_nodes (lon, lat);


CREATE TABLE IF NOT EXISTS "references".road_edges (
    id      SERIAL        PRIMARY KEY,
    source  INTEGER       NOT NULL
                REFERENCES "references".road_nodes(node_id) ON DELETE CASCADE,
    target  INTEGER       NOT NULL
                REFERENCES "references".road_nodes(node_id) ON DELETE CASCADE,
    weight  NUMERIC(12,6) NOT NULL CHECK (weight >= 0),  -- длина ребра в метрах
    CONSTRAINT road_edges_no_loop CHECK (source <> target)
);

COMMENT ON TABLE  "references".road_edges        IS 'Рёбра дорожного графа';
COMMENT ON COLUMN "references".road_edges.source IS 'ID начального узла → road_nodes.node_id';
COMMENT ON COLUMN "references".road_edges.target IS 'ID конечного узла → road_nodes.node_id';
COMMENT ON COLUMN "references".road_edges.weight IS 'Вес ребра (длина в метрах)';

CREATE INDEX IF NOT EXISTS idx_road_edges_source
    ON "references".road_edges (source);
CREATE INDEX IF NOT EXISTS idx_road_edges_target
    ON "references".road_edges (target);


-- =============================================================
-- 2. СКВАЖИНЫ (схема references — данные организаторов)
-- =============================================================

CREATE TABLE IF NOT EXISTS "references".wells (
    id        SERIAL       PRIMARY KEY,
    uwi       VARCHAR(50)  NOT NULL UNIQUE,     -- уникальный идентификатор скважины
    latitude  NUMERIC(12,8),                    -- широта (обезличена)
    longitude NUMERIC(12,8),                    -- долгота (обезличена)
    well_name VARCHAR(255),
    CONSTRAINT wells_coords_both_or_none
        CHECK ((latitude IS NULL) = (longitude IS NULL))
);

COMMENT ON TABLE  "references".wells           IS 'Справочник скважин и объектов работ';
COMMENT ON COLUMN "references".wells.uwi       IS 'Уникальный идентификатор скважины';
COMMENT ON COLUMN "references".wells.latitude  IS 'Широта (обезличена, метрически согласована)';
COMMENT ON COLUMN "references".wells.longitude IS 'Долгота';

CREATE INDEX IF NOT EXISTS idx_wells_coords
    ON "references".wells (latitude, longitude)
    WHERE latitude IS NOT NULL;


-- =============================================================
-- 3. СНАПШОТЫ WIALON (схема references — данные организаторов)
-- =============================================================

CREATE TABLE IF NOT EXISTS "references".wialon_units_snapshot_1 (
    id                 SERIAL           PRIMARY KEY,
    wialon_id          BIGINT           NOT NULL,
    nm                 TEXT,                          -- название техники
    cls                INTEGER,                       -- служебное поле Wialon
    mu                 INTEGER,                       -- служебное поле Wialon
    pos_t              BIGINT           NOT NULL,     -- Unix timestamp позиции (секунды)
    pos_y              DOUBLE PRECISION NOT NULL,     -- широта (Y-координата)
    pos_x              DOUBLE PRECISION NOT NULL,     -- долгота (X-координата)
    registration_plate TEXT,                          -- госномер (обезличен)
    payload_json       JSONB                          -- полный JSON снапшота Wialon
);

-- LIKE INCLUDING ALL копирует sequence из snapshot_1 и вызывает ошибку
-- при повторном ADD GENERATED. Создаём таблицы явно с собственным SERIAL.
CREATE TABLE IF NOT EXISTS "references".wialon_units_snapshot_2 (
    id                 SERIAL           PRIMARY KEY,
    wialon_id          BIGINT           NOT NULL,
    nm                 TEXT,
    cls                INTEGER,
    mu                 INTEGER,
    pos_t              BIGINT           NOT NULL,
    pos_y              DOUBLE PRECISION NOT NULL,
    pos_x              DOUBLE PRECISION NOT NULL,
    registration_plate TEXT,
    payload_json       JSONB
);

CREATE TABLE IF NOT EXISTS "references".wialon_units_snapshot_3 (
    id                 SERIAL           PRIMARY KEY,
    wialon_id          BIGINT           NOT NULL,
    nm                 TEXT,
    cls                INTEGER,
    mu                 INTEGER,
    pos_t              BIGINT           NOT NULL,
    pos_y              DOUBLE PRECISION NOT NULL,
    pos_x              DOUBLE PRECISION NOT NULL,
    registration_plate TEXT,
    payload_json       JSONB
);

COMMENT ON TABLE "references".wialon_units_snapshot_1 IS 'Снапшот парка техники из Wialon — срез 1';
COMMENT ON TABLE "references".wialon_units_snapshot_2 IS 'Снапшот парка техники из Wialon — срез 2';
COMMENT ON TABLE "references".wialon_units_snapshot_3 IS 'Снапшот парка техники из Wialon — срез 3';

COMMENT ON COLUMN "references".wialon_units_snapshot_1.wialon_id          IS 'ID единицы техники в Wialon';
COMMENT ON COLUMN "references".wialon_units_snapshot_1.pos_t              IS 'Unix timestamp позиции (секунды)';
COMMENT ON COLUMN "references".wialon_units_snapshot_1.pos_y              IS 'Широта (Y-координата)';
COMMENT ON COLUMN "references".wialon_units_snapshot_1.pos_x              IS 'Долгота (X-координата)';
COMMENT ON COLUMN "references".wialon_units_snapshot_1.registration_plate IS 'Госномер (обезличен)';
COMMENT ON COLUMN "references".wialon_units_snapshot_1.payload_json       IS 'Полный JSON снапшота Wialon';

CREATE INDEX IF NOT EXISTS idx_snap1_wialon_id ON "references".wialon_units_snapshot_1 (wialon_id);
CREATE INDEX IF NOT EXISTS idx_snap2_wialon_id ON "references".wialon_units_snapshot_2 (wialon_id);
CREATE INDEX IF NOT EXISTS idx_snap3_wialon_id ON "references".wialon_units_snapshot_3 (wialon_id);
CREATE INDEX IF NOT EXISTS idx_snap1_pos_t     ON "references".wialon_units_snapshot_1 (pos_t);
CREATE INDEX IF NOT EXISTS idx_snap2_pos_t     ON "references".wialon_units_snapshot_2 (pos_t);
CREATE INDEX IF NOT EXISTS idx_snap3_pos_t     ON "references".wialon_units_snapshot_3 (pos_t);


-- =============================================================
-- 4. ЗАЯВКИ (схема public — наши данные из CSV)
-- =============================================================
-- Структура строго по документу хакатона раздел 4.5
-- Связь: destination_uwi → references.wells.uwi

CREATE TABLE IF NOT EXISTS public.tasks (
    id                     SERIAL       PRIMARY KEY,
    task_id                VARCHAR(100) NOT NULL UNIQUE,
    priority               VARCHAR(10)  NOT NULL
                               CHECK (priority IN ('low', 'medium', 'high')),
    planned_start          TIMESTAMPTZ  NOT NULL,
    planned_duration_hours NUMERIC(8,2) NOT NULL
                               CHECK (planned_duration_hours > 0),
    destination_uwi        VARCHAR(50)  NOT NULL,   -- → references.wells.uwi
    task_type              VARCHAR(100) NOT NULL,
    shift                  VARCHAR(10)  NOT NULL
                               CHECK (shift IN ('day', 'night')),
    start_day              DATE         NOT NULL,
    -- Вычисляемое поле (хранится для удобства)
    -- end_day = start_day + ceil(planned_duration_hours / hours_in_shift)
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  public.tasks                        IS 'Заявки на выполнение работ (из CSV)';
COMMENT ON COLUMN public.tasks.task_id                IS 'Уникальный идентификатор заявки';
COMMENT ON COLUMN public.tasks.priority               IS 'Приоритет: low/medium/high. SLA: high +2ч, medium +5ч, low +12ч';
COMMENT ON COLUMN public.tasks.planned_start          IS 'Плановое время начала работ';
COMMENT ON COLUMN public.tasks.planned_duration_hours IS 'Плановая длительность работ (часы)';
COMMENT ON COLUMN public.tasks.destination_uwi        IS 'ID скважины → references.wells.uwi';
COMMENT ON COLUMN public.tasks.task_type              IS 'Тип работ (для проверки совместимости с техникой)';
COMMENT ON COLUMN public.tasks.shift                  IS 'Смена: day (08:00-20:00) / night (20:00-08:00)';
COMMENT ON COLUMN public.tasks.start_day              IS 'Дата начала выполнения';

CREATE INDEX IF NOT EXISTS idx_tasks_priority      ON public.tasks (priority);
CREATE INDEX IF NOT EXISTS idx_tasks_planned_start ON public.tasks (planned_start);
CREATE INDEX IF NOT EXISTS idx_tasks_start_day     ON public.tasks (start_day);
CREATE INDEX IF NOT EXISTS idx_tasks_destination   ON public.tasks (destination_uwi);
CREATE INDEX IF NOT EXISTS idx_tasks_task_type     ON public.tasks (task_type);

-- Автообновление updated_at
CREATE OR REPLACE FUNCTION public.update_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_tasks_updated_at ON public.tasks;
CREATE TRIGGER trg_tasks_updated_at
    BEFORE UPDATE ON public.tasks
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


-- =============================================================
-- 5. СЛОВАРЬ СОВМЕСТИМОСТИ (схема public)
-- =============================================================

CREATE TABLE IF NOT EXISTS public.compatibility (
    id            SERIAL       PRIMARY KEY,
    task_type     VARCHAR(100) NOT NULL UNIQUE,
    vehicle_types TEXT[]       NOT NULL,
    description   TEXT
);

COMMENT ON TABLE  public.compatibility              IS 'Словарь совместимости типов работ и техники';
COMMENT ON COLUMN public.compatibility.task_type    IS 'Тип работ';
COMMENT ON COLUMN public.compatibility.vehicle_types IS 'Паттерны имён совместимой техники';

-- =============================================================
-- 6. НАЗНАЧЕНИЯ (схема public — результаты работы сервиса)
-- =============================================================

CREATE TABLE IF NOT EXISTS public.assignments (
    id          SERIAL       PRIMARY KEY,
    task_id     VARCHAR(100) NOT NULL REFERENCES public.tasks(task_id) ON DELETE CASCADE,
    wialon_id   BIGINT       NOT NULL,
    assigned_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    algorithm   VARCHAR(50)  NOT NULL DEFAULT 'manual',  -- manual/greedy/vrp/optimized/simulated
    score       NUMERIC(5,4),
    travel_km   NUMERIC(10,3),
    eta_minutes NUMERIC(8,1),
    reason      TEXT,
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE
);

COMMENT ON TABLE  public.assignments           IS 'История назначений техники на заявки';
COMMENT ON COLUMN public.assignments.algorithm IS 'Алгоритм: manual/greedy/vrp/optimized/simulated';
COMMENT ON COLUMN public.assignments.score     IS 'Скор кандидата [0,1]';
COMMENT ON COLUMN public.assignments.is_active IS 'Активное назначение (false = отменено/переназначено)';

CREATE INDEX IF NOT EXISTS idx_assignments_task_id ON public.assignments (task_id);
CREATE INDEX IF NOT EXISTS idx_assignments_vehicle ON public.assignments (wialon_id);
CREATE INDEX IF NOT EXISTS idx_assignments_active  ON public.assignments (is_active) WHERE is_active;


COMMIT;
