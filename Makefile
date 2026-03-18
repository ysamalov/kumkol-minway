DB_USER   = postgres
DB_NAME   = minwaykumkoldb
PSQL      = docker compose exec -T db psql -U $(DB_USER) -d $(DB_NAME)

# ─────────────────────────────────────────────
# Запуск / остановка
# ─────────────────────────────────────────────

up:
	docker compose up --build -d

down:
	docker compose down

reset:
	docker compose down -v
	docker compose up --build -d

logs:
	docker compose logs -f

# ─────────────────────────────────────────────
# Загрузка данных
# ─────────────────────────────────────────────

load-test-data:
	@echo "Загружаем тестовые данные (граф, скважины, снапшоты)..."
	$(PSQL) < test_data.sql
	@echo "OK"

load-tasks:
	@echo "Загружаем заявки из tasks.csv..."
	$(PSQL) -c "\copy public.tasks(task_id, priority, planned_start, planned_duration_hours, destination_uwi, task_type, shift, start_day) FROM '/dev/stdin' CSV HEADER" < tasks.csv
	@echo "OK"

load-all: load-test-data load-tasks
	@echo "Все данные загружены"

load-hackathon:
	@echo ">>> [1/4] Очищаем старые данные..."
	$(PSQL) -c "TRUNCATE \"references\".road_nodes, \"references\".road_edges, \"references\".wells CASCADE;"
	$(PSQL) -c "TRUNCATE \"references\".wialon_units_snapshot_1, \"references\".wialon_units_snapshot_2, \"references\".wialon_units_snapshot_3 CASCADE;"
	$(PSQL) -c "TRUNCATE public.tasks, public.assignments CASCADE;"
	@echo ">>> [2/4] Загружаем граф, скважины и снапшоты..."
	$(PSQL) < DB/load_references.sql
	@echo ">>> [3/4] Загружаем справочники техники и совместимости..."
	$(PSQL) < DB/update_catalogs.sql
	@echo ">>> [4/5] Загружаем заявки..."
	$(PSQL) < DB/tasks_hackathon.sql
	@echo ">>> [5/5] Корректируем координаты техники..."
	$(PSQL) < DB/relocate_vehicles.sql
	@echo ""
	@echo "=== Готово! Проверка: ==="
	$(PSQL) -c "\
		SELECT 'road_nodes'      AS table_name, COUNT(*) FROM \"references\".road_nodes \
		UNION ALL SELECT 'road_edges',           COUNT(*) FROM \"references\".road_edges \
		UNION ALL SELECT 'wells',                COUNT(*) FROM \"references\".wells \
		UNION ALL SELECT 'snapshot_1',           COUNT(*) FROM \"references\".wialon_units_snapshot_1 \
		UNION ALL SELECT 'tasks',                COUNT(*) FROM public.tasks \
		UNION ALL SELECT 'vehicle_registry',     COUNT(*) FROM public.vehicle_registry \
		UNION ALL SELECT 'compatibility',        COUNT(*) FROM public.compatibility;"

# ─────────────────────────────────────────────
# Утилиты
# ─────────────────────────────────────────────

psql:
	docker compose exec db psql -U $(DB_USER) -d $(DB_NAME)

check:
	@echo "=== road_nodes ===" && $(PSQL) -c "SELECT COUNT(*) FROM \"references\".road_nodes;"
	@echo "=== wells ===" && $(PSQL) -c "SELECT COUNT(*) FROM \"references\".wells;"
	@echo "=== snapshots ===" && $(PSQL) -c "SELECT COUNT(*) FROM \"references\".wialon_units_snapshot_1;"
	@echo "=== tasks ===" && $(PSQL) -c "SELECT COUNT(*) FROM public.tasks;"
	@echo "=== vehicle_registry ===" && $(PSQL) -c "SELECT wialon_id, registration_plate, vehicle_type, type_source FROM public.vehicle_registry ORDER BY wialon_id;"

.PHONY: up down reset logs load-test-data load-tasks load-all load-hackathon psql check