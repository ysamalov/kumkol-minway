DB_USER = postgres
DB_NAME = minwaykumkoldb
PSQL    = docker compose exec -T db psql -U $(DB_USER) -d $(DB_NAME) -v ON_ERROR_STOP=1

# ─────────────────────────────────────────────
# Запуск / остановка
# ─────────────────────────────────────────────

up:
	docker compose up --build -d

down:
	docker compose down

# Полный сброс: удаляет тома (БД), пересобирает образ
reset:
	docker compose down -v
	docker compose up --build -d

logs:
	docker compose logs -f

# ─────────────────────────────────────────────
# Загрузка данных
# ─────────────────────────────────────────────

# Шаг 1: базовые справочники (ДО снапшотов — FK триггера)
load-hackathon:
	@echo ">>> [1/5] Очищаем таблицы..."
	$(PSQL) -c "TRUNCATE public.assignments CASCADE;"
	$(PSQL) -c "TRUNCATE public.tasks CASCADE;"
	$(PSQL) -c "TRUNCATE public.vehicle_registry CASCADE;"
	$(PSQL) -c "TRUNCATE public.vehicle_type_catalog CASCADE;"
	$(PSQL) -c "TRUNCATE \"references\".wialon_units_snapshot_1, \"references\".wialon_units_snapshot_2, \"references\".wialon_units_snapshot_3;"
	$(PSQL) -c "TRUNCATE \"references\".road_edges, \"references\".road_nodes, \"references\".wells;"
	@echo ">>> [2/5] vehicle_type_catalog + detect_vehicle_type() (нужны до снапшотов)..."
	$(PSQL) < DB/init_catalogs.sql
	@echo ">>> [3/5] Граф дорог + скважины + снапшоты техники..."
	$(PSQL) < DB/load_references.sql
	@echo ">>> [4/5] vehicle_registry + compatibility..."
	$(PSQL) < DB/update_catalogs.sql
	@echo ">>> [5/5] Оригинальные заявки хакатона..."
	$(PSQL) < DB/tasks_hackathon.sql

# Шаг 2: демо-данные поверх базовых
load-demo:
	@echo ">>> [1/3] 200 демо-заявок на 2026-04-15..."
	$(PSQL) < DB/demo_tasks.sql
	@echo ">>> [2/3] Техника на позициях конца смены 2026-04-14 (узлы графа)..."
	$(PSQL) < DB/relocate_vehicles_demo.sql
	@echo ">>> [3/3] Спецтехника (АНЦ, XJ, экскаваторы, бульдозеры...)..."
	$(PSQL) < DB/add_demo_vehicles.sql
	@echo ""
	@echo "=== Итог ==="
	$(PSQL) -c "SELECT 'vehicles' AS t, COUNT(*) FROM public.vehicle_registry UNION ALL SELECT 'tasks', COUNT(*) FROM public.tasks UNION ALL SELECT 'road_nodes', COUNT(*) FROM \"references\".road_nodes;"
	@echo ">>> Перезагружаем сервер..."
	sleep 3
	curl -s -X POST http://localhost:8090/api/reload | python3 -m json.tool || docker compose restart server

# Полная загрузка с нуля (запускать после make reset)
load-all-demo: load-hackathon load-demo

# ─────────────────────────────────────────────
# Утилиты
# ─────────────────────────────────────────────

psql:
	docker compose exec db psql -U $(DB_USER) -d $(DB_NAME)

check:
	$(PSQL) -c "SELECT 'road_nodes' AS t, COUNT(*) FROM \"references\".road_nodes UNION ALL SELECT 'road_edges', COUNT(*) FROM \"references\".road_edges UNION ALL SELECT 'snapshot_1', COUNT(*) FROM \"references\".wialon_units_snapshot_1 UNION ALL SELECT 'vehicle_registry', COUNT(*) FROM public.vehicle_registry UNION ALL SELECT 'tasks', COUNT(*) FROM public.tasks UNION ALL SELECT 'compatibility', COUNT(*) FROM public.compatibility;"

.PHONY: up down reset logs load-hackathon load-demo load-all-demo psql check
