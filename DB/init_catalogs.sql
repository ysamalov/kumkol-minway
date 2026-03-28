-- =============================================================
-- init_catalogs.sql
-- Шаг 1 из load-hackathon: заполнить vehicle_type_catalog
-- и пересоздать detect_vehicle_type() ДО загрузки снапшотов.
-- Триггер sync_vehicle_registry требует наличия всех типов в
-- vehicle_type_catalog (FK), иначе INSERT падает с ошибкой.
-- =============================================================

BEGIN;

-- 1. Каталог типов техники (без CASCADE — не трогаем зависимые таблицы)
INSERT INTO public.vehicle_type_catalog
    (vehicle_type, description, avg_speed_kmh, can_work_night)
VALUES
    ('Автобус_Большой', 'Hyundai Universe, Daewoo BH 120F, Yutong — большой автобус', 60.0, FALSE),
    ('Автобус_Средний', 'Hyundai County — средний автобус (20-30 мест)',               55.0, FALSE),
    ('Автобус_Малый',   'Toyota Coaster, Toyota Hiace — малый автобус (до 20 мест)',   55.0, FALSE),
    ('АНЦ',             'Агрегат насосно-цементировочный АНЦ-32/50',                   35.0, TRUE),
    ('АЦН',             'Автоцистерна насосная (водовоз)',                              35.0, TRUE),
    ('ЦА',              'Цементировочный агрегат ЦА-320',                              30.0, TRUE),
    ('АСЦ',             'Агрегат самоходный цементировочный АСЦ-320',                  30.0, TRUE),
    ('УНБ',             'Установка насосного блока',                                   30.0, TRUE),
    ('БМ',              'Буровая машина БМ-70К/70-32',                                 25.0, TRUE),
    ('БАРС',            'Буровой агрегат БАРС-80/100',                                 25.0, TRUE),
    ('XJ',              'Подъёмный агрегат XJ350/450/900, ZYT',                        30.0, TRUE),
    ('АКС',             'Агрегат колтюбинговый АКС',                                   30.0, TRUE),
    ('ППУА',            'Передвижная пароподогревательная установка',                   30.0, TRUE),
    ('Автокран',        'Автокран КС, 50т и др.',                                       40.0, TRUE),
    ('Бульдозер',       'Бульдозер (любая марка)',                                      15.0, TRUE),
    ('Экскаватор',      'Экскаватор гусеничный/колесный',                              12.0, TRUE),
    ('Автогрейдер',     'Автогрейдер',                                                  20.0, TRUE),
    ('Каток',           'Каток дорожный',                                               15.0, TRUE),
    ('Погрузчик',       'Погрузчик фронтальный',                                       20.0, TRUE),
    ('Самосвал',        'Самосвал',                                                     50.0, TRUE),
    ('Трубоукладчик',   'Трубоукладчик/трубокладчик',                                  15.0, TRUE),
    ('Бортовой',        'Бортовой автомобиль',                                          60.0, TRUE),
    ('Пикап',           'Пикап (служебный)',                                            80.0, TRUE),
    ('Топливозаправщик','Топливозаправщик',                                             50.0, TRUE),
    ('Гидроподъёмник',  'А/гидроподъёмник, АРОК',                                      40.0, TRUE),
    ('UNKNOWN',         'Тип не определён — требует ручного уточнения',                40.0, TRUE)
ON CONFLICT (vehicle_type) DO UPDATE SET
    description    = EXCLUDED.description,
    avg_speed_kmh  = EXCLUDED.avg_speed_kmh,
    can_work_night = EXCLUDED.can_work_night;

-- 2. Функция определения типа из nm — нужна триггеру до загрузки снапшотов
CREATE OR REPLACE FUNCTION public.detect_vehicle_type(nm TEXT)
RETURNS VARCHAR(50)
LANGUAGE plpgsql IMMUTABLE
AS $$
DECLARE
    nm_upper TEXT := UPPER(COALESCE(nm, ''));
BEGIN
    IF nm_upper LIKE '%UNIVERSE%' OR nm_upper LIKE '%YUTONG%'
       OR nm_upper LIKE '%DAEWOO BH%' OR nm_upper LIKE '%BH 120%'
       OR nm_upper LIKE '%BH-120%'                          THEN RETURN 'Автобус_Большой'; END IF;
    IF nm_upper LIKE '%COUNTY%'                             THEN RETURN 'Автобус_Средний'; END IF;
    IF nm_upper LIKE '%COASTER%' OR nm_upper LIKE '%HIACE%' THEN RETURN 'Автобус_Малый';  END IF;
    IF nm_upper LIKE '%АНЦ%'                                THEN RETURN 'АНЦ';             END IF;
    IF nm_upper LIKE '%АСЦ%'                                THEN RETURN 'АСЦ';             END IF;
    IF nm_upper LIKE '%АЦН%'                                THEN RETURN 'АЦН';             END IF;
    IF nm_upper LIKE '%ЦА-%'                                THEN RETURN 'ЦА';              END IF;
    IF nm_upper LIKE '%УНБ%'                                THEN RETURN 'УНБ';             END IF;
    IF nm_upper LIKE '%БМ-70%' OR nm_upper LIKE '%БМ70%'   THEN RETURN 'БМ';              END IF;
    IF nm_upper LIKE '%БАРС%'                               THEN RETURN 'БАРС';            END IF;
    IF nm_upper LIKE '%XJ%' OR nm_upper LIKE '%ZYT%'        THEN RETURN 'XJ';              END IF;
    IF nm_upper LIKE '%АКС%'                                THEN RETURN 'АКС';             END IF;
    IF nm_upper LIKE '%ППУА%'                               THEN RETURN 'ППУА';            END IF;
    IF nm_upper LIKE '%КРАН%'                               THEN RETURN 'Автокран';        END IF;
    IF nm_upper LIKE '%БУЛЬДОЗЕР%'                          THEN RETURN 'Бульдозер';       END IF;
    IF nm_upper LIKE '%ЭКСКАВАТОР%'                         THEN RETURN 'Экскаватор';      END IF;
    IF nm_upper LIKE '%ГРЕЙДЕР%'                            THEN RETURN 'Автогрейдер';     END IF;
    IF nm_upper LIKE '%КАТОК%'                              THEN RETURN 'Каток';           END IF;
    IF nm_upper LIKE '%ПОГРУЗЧИК%'                          THEN RETURN 'Погрузчик';       END IF;
    IF nm_upper LIKE '%САМОСВАЛ%'                           THEN RETURN 'Самосвал';        END IF;
    IF nm_upper LIKE '%ШАКМАН%' OR nm_upper LIKE '%SHACMAN%' THEN RETURN 'Самосвал';      END IF;
    IF nm_upper LIKE '%ТРУБОУКЛАДЧИК%' OR nm_upper LIKE '%ТРУБОКЛАД%'
                                                            THEN RETURN 'Трубоукладчик';   END IF;
    IF nm_upper LIKE '%ПИКАП%'                              THEN RETURN 'Пикап';           END IF;
    IF nm_upper LIKE '%ТОПЛИВО%' OR nm_upper LIKE '%ЗАПРА%' THEN RETURN 'Топливозаправщик'; END IF;
    IF nm_upper LIKE '%БОРТОВОЙ%' OR nm_upper LIKE '%БОРТОВ%' THEN RETURN 'Бортовой';       END IF;
    IF nm_upper LIKE '%ПАЗ%' OR nm_upper LIKE '%PAZ%'      THEN RETURN 'Автобус_Малый';  END IF;
    IF nm_upper LIKE '%ГАЗЕЛЬ%' OR nm_upper LIKE '%GAZEL%'  THEN RETURN 'Автобус_Малый';  END IF;
    RETURN 'UNKNOWN';
END;
$$;

COMMIT;

SELECT 'vehicle_type_catalog' AS table_name, COUNT(*) FROM public.vehicle_type_catalog;
