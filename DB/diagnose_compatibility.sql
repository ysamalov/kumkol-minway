-- =====================================================================
-- ДИАГНОСТИКА: запустите это и покажите результат
-- Выполнить: make psql
-- Затем скопировать содержимое этого файла в консоль psql
-- =====================================================================

-- 1. Что в таблице compatibility для Отбор керна?
SELECT task_type, vehicle_types 
FROM public.compatibility 
WHERE task_type LIKE '%керна%' OR task_type LIKE '%Отбор%';

-- 2. Что в vehicle_registry для Самосвала?
SELECT wialon_id, vehicle_type, display_name 
FROM public.vehicle_registry 
WHERE vehicle_type = 'Самосвал' OR display_name LIKE '%SHACMAN%';

-- 3. Есть ли Самосвал в списке для СК5-3?
SELECT task_type, vehicle_types 
FROM public.compatibility 
WHERE 'Самосвал' = ANY(vehicle_types);

-- 4. Полная таблица compatibility
SELECT task_type, vehicle_types 
FROM public.compatibility 
ORDER BY task_type;

-- 5. Счётчики
SELECT 'compatibility' AS t, COUNT(*) FROM public.compatibility
UNION ALL SELECT 'vehicle_registry', COUNT(*) FROM public.vehicle_registry
UNION ALL SELECT 'snapshot_1', COUNT(*) FROM "references".wialon_units_snapshot_1;
