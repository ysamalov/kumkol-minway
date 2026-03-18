#!/bin/bash
# =============================================================
# Скрипт загрузки данных хакатона
# Использование: DATABASE_URL=postgresql://... bash load_hackathon_data.sh
# =============================================================

set -e
DB="${DATABASE_URL:-postgresql://postgres:postgres@localhost:5432/postgres}"

echo "=== Шаг 1: Применяем схему (если ещё не накатана) ==="
psql "$DB" -f migrations/001_init.sql
psql "$DB" -f migrations/002_vehicle_registry.sql

echo "=== Шаг 2: Очищаем старые тестовые данные ==="
psql "$DB" << 'SQL'
TRUNCATE "references".road_nodes, "references".road_edges, "references".wells CASCADE;
TRUNCATE "references".wialon_units_snapshot_1,
         "references".wialon_units_snapshot_2,
         "references".wialon_units_snapshot_3 CASCADE;
TRUNCATE public.tasks, public.assignments CASCADE;
SQL

echo "=== Шаг 3: Загружаем дамп организаторов (граф + скважины + техника) ==="
psql "$DB" -f mock_uto_backup.sql

echo "=== Шаг 4: Обновляем справочники под реальные данные ==="
psql "$DB" -f update_catalogs.sql

echo "=== Шаг 5: Загружаем заявки хакатона ==="
psql "$DB" -f tasks_hackathon.sql

echo "=== Шаг 6: Корректируем координаты техники в район месторождения ==="
psql "$DB" -f DB/relocate_vehicles.sql

echo "=== Готово! Проверка: ==="
psql "$DB" << 'SQL'
SELECT 'road_nodes'        AS table_name, COUNT(*) FROM "references".road_nodes
UNION ALL SELECT 'road_edges',            COUNT(*) FROM "references".road_edges
UNION ALL SELECT 'wells',                 COUNT(*) FROM "references".wells
UNION ALL SELECT 'snapshot_1',            COUNT(*) FROM "references".wialon_units_snapshot_1
UNION ALL SELECT 'tasks',                 COUNT(*) FROM public.tasks
UNION ALL SELECT 'vehicle_type_catalog',  COUNT(*) FROM public.vehicle_type_catalog
UNION ALL SELECT 'vehicle_registry',      COUNT(*) FROM public.vehicle_registry
UNION ALL SELECT 'compatibility',         COUNT(*) FROM public.compatibility;
SQL