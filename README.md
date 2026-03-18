# ИС УТО — Интеллектуальная система маршрутизации спецтехники

Сервис подбора и маршрутизации спецтехники на нефтяном месторождении. Принимает заявки на выполнение работ и возвращает ранжированный список подходящих единиц техники с маршрутом, ETA и объяснением выбора.

**Стек:** Python 3.11 · FastAPI · asyncpg · NetworkX · SciPy · PostgreSQL 16 · Docker

---

## Содержание

1. [Структура проекта](#структура-проекта)
2. [Требования](#требования)
3. [Лишние файлы перед деплоем](#лишние-файлы-перед-деплоем)
4. [Переменные окружения](#переменные-окружения)
5. [Запуск через Docker (рекомендуется)](#запуск-через-docker-рекомендуется)
6. [Загрузка данных в базу](#загрузка-данных-в-базу)
7. [Проверка работоспособности](#проверка-работоспособности)
8. [Запуск без Docker](#запуск-без-docker)
9. [API Reference](#api-reference)
10. [Алгоритмы](#алгоритмы)
11. [Устранение проблем](#устранение-проблем)

---

## Структура проекта

```
kumkol-minway/
├── app/
│   ├── main.py                   — FastAPI приложение, lifespan (старт БД, граф, парк)
│   ├── dependencies.py           — DI: репозиторий, граф, флит
│   ├── ai/
│   │   ├── analytics.py          — анализ скоростей и простоев техники
│   │   └── explainer.py          — AI-объяснение рекомендаций (OpenRouter)
│   ├── api/routes/
│   │   └── recommendations.py    — все HTTP-эндпоинты
│   ├── core/
│   │   ├── config.py             — настройки (pydantic-settings, .env)
│   │   ├── exceptions.py         — кастомные исключения
│   │   └── models.py             — Pydantic-модели (домен + API-схемы)
│   ├── db/
│   │   └── repository.py         — все async SQL-запросы (asyncpg)
│   ├── fleet/
│   │   └── manager.py            — состояние парка: снапшоты, скорости, совместимость
│   ├── graph/
│   │   └── road_graph.py         — граф дорог: NetworkX Dijkstra + SciPy KDTree
│   ├── optimizer/
│   │   ├── optimizer.py          — recommend / group_tasks / greedy_baseline
│   │   ├── simulator.py          — симулятор движения техники
│   │   └── vrp_solver.py         — VRP-солвер на OR-Tools
│   ├── scoring/
│   │   └── scorer.py             — формула скоринга [0,1]
│   └── services/
│       ├── optimize_service.py   — сервис оптимизации маршрутов
│       ├── recommendation_service.py — сервис рекомендаций
│       ├── route_service.py      — сервис маршрутизации
│       └── task_service.py       — сервис работы с заявками
├── migrations/
│   ├── 001_init.sql              — основная схема (tasks, compatibility, etc.)
│   └── 002_vehicle_registry.sql  — реестр техники + триггеры авто-заполнения
├── DB/                           — данные для загрузки (подробнее ниже)
├── app/static/
│   ├── map.html                  — интерактивная карта маршрутов
│   └── field_map.html            — карта месторождения
├── .env.example                  — шаблон переменных окружения
├── docker-compose.yml
├── Dockerfile
├── Makefile                      — удобные команды для управления
├── requirements.txt
└── requirements-test.txt
```

---

## Требования

| Компонент | Версия |
|-----------|--------|
| Docker | 24+ |
| Docker Compose | 2.20+ |
| make | любая |
| (опционально) Python | 3.11+ |
| (опционально) PostgreSQL client | 15+ |

---


### ✅ Файлы для деплоя

- `DB/load_references.sql` — граф дорог + скважины + снапшоты техники (1.3 МБ INSERT-ов)
- `DB/update_catalogs.sql` — справочники типов техники и совместимости
- `DB/tasks_hackathon.sql` — 137 тестовых заявок
- `DB/relocate_vehicles.sql` — коррекция координат техники в район месторождения
- `migrations/001_init.sql` и `migrations/002_vehicle_registry.sql` — схема БД (применяются автоматически при старте контейнера `db`)

---

## Переменные окружения

Скопируйте шаблон и заполните:

```bash
cp .env.example .env
```

Обязательные параметры:

```dotenv
# PostgreSQL (при использовании Docker оставьте DB_HOST=db)
DB_HOST=db
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=ВАШ_ПАРОЛЬ        # Смените на продакшне!
DB_NAME=minwaykumkoldb

# AI-объяснения (можно использовать бесплатный ключ с openrouter.ai)
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=stepfun/step-3.5-flash:free
```

Остальные параметры (скорости, SLA, веса скоринга, таймауты) имеют разумные значения по умолчанию из `.env.example`

> **Получить ключ OpenRouter:** зарегистрируйтесь на [openrouter.ai](https://openrouter.ai), создайте API-ключ в разделе Keys. Бесплатная модель `stepfun/step-3.5-flash:free` работает без пополнения баланса.

---

## Запуск через Docker (рекомендуется)

### 1. Подготовка

```bash
git clone <репозиторий> kumkol-minway
cd kumkol-minway
cp .env.example .env
# Отредактируйте .env: укажите DB_PASSWORD и OPENROUTER_API_KEY
nano .env
```

### 2. Сборка и запуск

```bash
make up
# или напрямую:
docker compose up --build -d
```

Это запустит два контейнера:
- `db` — PostgreSQL 16, порт `5433` на хосте (чтобы не конфликтовать с локальным postgres)
- `server` — FastAPI приложение, порт `8080`

Docker автоматически применит миграции `migrations/001_init.sql` и `migrations/002_vehicle_registry.sql` при первом старте контейнера `db`.

### 3. Дождаться готовности

```bash
make logs
# или:
docker compose logs -f
```

Приложение готово, когда в логах появится:
```
INFO:     Application startup complete.
```

Проверьте health:
```bash
curl http://localhost:8080/api/health
```

### 4. Загрузить данные в базу

```bash
make load-hackathon
```

Эта команда выполняет 4 шага:
1. Очищает старые данные (TRUNCATE)
2. Загружает граф дорог, скважины и снапшоты техники (`load_references.sql`)
3. Загружает справочники техники и таблицу совместимости (`update_catalogs.sql`)
4. Загружает 137 тестовых заявок (`tasks_hackathon.sql`)

В конце выводится таблица с количеством строк в каждой таблице — проверьте, что все числа ненулевые.

Ожидаемый результат:
```
 table_name        | count
-------------------+-------
 road_nodes        | ~1500
 road_edges        | ~3000
 wells             | ~2500
 snapshot_1        | ~85
 tasks             | 137
 vehicle_registry  | ~85
 compatibility     | ~45
```

## Загрузка данных в базу

### Порядок загрузки (важно соблюдать)

```
migrations/001_init.sql          ← применяется автоматически Docker
migrations/002_vehicle_registry.sql ← применяется автоматически Docker
        ↓
DB/load_references.sql           ← граф дорог, скважины, снапшоты
        ↓
DB/update_catalogs.sql           ← типы техники, совместимость (зависит от снапшотов)
        ↓
DB/tasks_hackathon.sql           ← заявки (зависят от wells)
        ↓
DB/relocate_vehicles.sql         ← техники (торректирует местоположение)
```

**Почему именно такой порядок:** `update_catalogs.sql` перезаполняет `vehicle_registry` из снапшотов — снапшоты должны быть загружены раньше. `tasks_hackathon.sql` ссылается на `destination_uwi` из таблицы `wells`.

### Ручная загрузка без Makefile

```bash
DATABASE_URL="postgresql://postgres:ПАРОЛЬ@localhost:5433/minwaykumkoldb"

psql "$DATABASE_URL" -f DB/load_references.sql
psql "$DATABASE_URL" -f DB/update_catalogs.sql
psql "$DATABASE_URL" -f DB/tasks_hackathon.sql
```

### Что содержат файлы БД

| Файл | Содержимое | Размер |
|---|---|---|
| `migrations/001_init.sql` | Схема: `tasks`, `assignments`, `compatibility`, `wialon_units_snapshot_*` | — |
| `migrations/002_vehicle_registry.sql` | Схема: `vehicle_type_catalog`, `vehicle_registry`, функция `detect_vehicle_type`, триггеры | — |
| `DB/load_references.sql` | INSERT для `road_nodes`, `road_edges`, `wells`, `wialon_units_snapshot_1/2/3` | 1.3 МБ |
| `DB/update_catalogs.sql` | INSERT для `vehicle_type_catalog`, `compatibility`; перезаполнение `vehicle_registry` | 17 КБ |
| `DB/tasks_hackathon.sql` | 137 тестовых заявок в `public.tasks` | 23 КБ |
| `DB/relocate_vehicles.sql` | UPDATE координат техники для отображения на карте месторождения | 38 КБ |

---

## Проверка работоспособности

```bash
# Health-check
curl http://localhost:8080/api/health
# Ожидаемый ответ:
# {"status":"ok","vehicles":85,"graph_nodes":1500,"graph_edges":3000}

# Swagger UI
open http://localhost:8080/docs

# Карта месторождения
open http://localhost:8080/static/field_map.html

# Проверка данных в БД
make check
```

---

## API Reference

### GET /api/health

Проверка состояния системы.

```json
{
  "status": "ok",
  "vehicles": 85,
  "graph_nodes": 1500,
  "graph_edges": 3000
}
```

---

### POST /api/recommendations

Подобрать технику для заявки.

**Запрос:**
```json
{
  "task_id": "BBJ-8414",
  "priority": "high",
  "destination_uwi": "JET_2023",
  "planned_start": "2025-07-01T08:00:00Z",
  "duration_hours": 7.0,
  "task_type": "Отсыпка мергелем"
}
```

**Ответ:**
```json
{
  "units": [
    {
      "wialon_id": 10234,
      "name": "Самосвал А045КМ",
      "eta_minutes": 38,
      "distance_km": 12.4,
      "score": 0.92,
      "reason": "совместима по типу работ; свободна; расстояние 12.4 км, ETA 38 мин"
    }
  ]
}
```

Поле `priority`: `"high"` | `"medium"` | `"low"`

---

### POST /api/route

Построить маршрут от единицы техники до объекта.

**Запрос:**
```json
{
  "from": {"wialon_id": 10234, "lon": 55.18, "lat": 46.65},
  "to":   {"uwi": "JET_2023"}
}
```

**Ответ:**
```json
{
  "distance_km": 12.4,
  "time_minutes": 38.0,
  "nodes": [4501, 4498, 4472, 4431],
  "coords": [[55.18, 46.65], [55.17, 46.66]]
}
```

---

### POST /api/multitask

Сгруппировать несколько заявок для выполнения одной машиной.

**Запрос:**
```json
{
  "task_ids": ["BBJ-8414", "BBJ-8417", "BBJ-8419"],
  "constraints": {
    "max_total_time_minutes": 480,
    "max_detour_ratio": 1.3
  }
}
```

**Ответ:**
```json
{
  "groups": [["BBJ-8414", "BBJ-8417"], ["BBJ-8419"]],
  "strategy_summary": "mixed",
  "total_distance_km": 41.2,
  "total_time_minutes": 195.0,
  "baseline_distance_km": 56.8,
  "baseline_time_minutes": 244.0,
  "savings_percent": 27.5,
  "reason": "заявки BBJ-8414 и BBJ-8417 в 3.1 км (крюк 18%); суммарная экономия 27.5%"
}
```

---

### POST /api/optimize

VRP-оптимизация: распределить список заявок по всему парку техники.

**Запрос:**
```json
{
  "task_ids": ["BBJ-8414", "BBJ-8417", "BBJ-8419", "BBJ-8420"],
  "time_limit_sec": 10
}
```

---

## Алгоритмы

### Граф и маршрутизация

| Задача | Алгоритм | Сложность |
|--------|----------|-----------|
| Кратчайший путь | Dijkstra (NetworkX) | O((V+E) log V) |
| Привязка координат к узлу | KDTree (SciPy) | O(log N) |
| Матрица расстояний (batch) | multi-source Dijkstra | O(S × (V+E) log V) |
| VRP (распределение заявок) | OR-Tools CP-SAT | NP-hard, time limit |

### Формула скоринга

```
score = 1 / (1 + cost)

cost = SCORE_W_DIST  × dist_penalty      # расстояние / SCORE_DIST_REFERENCE_KM, cap 1.0
     + SCORE_W_WAIT  × wait_penalty      # wait_min / SLA_deadline_min
     + SCORE_W_IDLE  × idle_penalty      # коэффициент простоя
     + SCORE_W_PRIO  × prio_penalty      # риск нарушения SLA × множитель приоритета
     + SCORE_W_COMPAT × compat_penalty   # 0 если совместима, 1 если нет

Веса по умолчанию: dist=0.35, wait=0.25, idle=0.10, prio=0.20, compat=0.10
Дедлайны SLA: high=2ч, medium=5ч, low=12ч
```

### Группировка заявок (multitask)

1. Union-Find кластеризация пар по `detour_ratio ≤ max_detour_ratio`
2. Greedy TSP (nearest-neighbour) внутри каждой группы
3. Сравнение с baseline (каждая заявка отдельно)

---

## Устранение проблем

### Приложение стартует, но `vehicles: 0` в /health

Данные не загружены. Выполните:
```bash
make load-hackathon
```

### Ошибка `relation "references".road_nodes does not exist`

Миграции не применились. Пересоздайте контейнеры:
```bash
make reset
# Дождаться старта, затем:
make load-hackathon
```

### `Connection refused` при подключении к БД

Контейнер `db` ещё не готов. Подождите 10–15 секунд и повторите. Проверьте статус:
```bash
docker compose ps
```

### `invalid input syntax` при загрузке `load_references.sql`

Убедитесь, что кодировка файла UTF-8 и клиент psql настроен на UTF-8:
```bash
PGCLIENTENCODING=UTF8 psql "$DATABASE_URL" -f DB/load_references.sql
```

### Техника отображается не в районе месторождения на карте

Примените скрипт смещения координат:
```bash
docker compose exec -T db psql -U postgres -d minwaykumkoldb < DB/relocate_vehicles.sql
```

### Сброс к чистому состоянию

```bash
make reset
# Удаляет volume с данными PostgreSQL и пересобирает всё с нуля
```

---

## Полезные команды (Makefile)

```bash
make up              # Собрать и запустить все контейнеры
make down            # Остановить контейнеры
make reset           # Полный сброс (удаляет данные БД!)
make logs            # Показать логи в реальном времени
make load-hackathon  # Загрузить все данные хакатона
make check           # Проверить количество записей в таблицах
make psql            # Открыть psql в контейнере БД
```