from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import asyncpg
from app.core.config import settings

from app.core.models import Task, Vehicle, Well, Priority, Shift


# Schema for organizer tables (road_nodes, road_edges, wells, wialon_snapshots)
# "references" is a reserved word in PostgreSQL — must be quoted
# Change to 'public' if running with local test data (no organizer DB)
SCHEMA = '"references"'  # таблицы графа, скважин и снапшотов живут в схеме references


class Repository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    # ── Wells ────────────────────────────────────────────────────────────────

    async def get_well_by_uwi(self, uwi: str) -> Optional[dict]:
        row = await self.pool.fetchrow(
            f'SELECT uwi, latitude, longitude, well_name '
            f'FROM {SCHEMA}.wells WHERE uwi = $1',
            uwi,
        )
        return dict(row) if row else None

    async def get_all_wells(self) -> list[dict]:
        rows = await self.pool.fetch(
            f'SELECT uwi, latitude, longitude, well_name '
            f'FROM {SCHEMA}.wells WHERE latitude IS NOT NULL'
        )
        return [dict(r) for r in rows]

    # ── Snapshots ────────────────────────────────────────────────────────────

    async def get_latest_snapshots(self) -> list[dict]:
        rows = await self.pool.fetch(f"""
            WITH combined AS (
                SELECT wialon_id, nm, pos_t, pos_y, pos_x, registration_plate
                FROM {SCHEMA}.wialon_units_snapshot_1
                UNION ALL
                SELECT wialon_id, nm, pos_t, pos_y, pos_x, registration_plate
                FROM {SCHEMA}.wialon_units_snapshot_2
                UNION ALL
                SELECT wialon_id, nm, pos_t, pos_y, pos_x, registration_plate
                FROM {SCHEMA}.wialon_units_snapshot_3
            ),
            ranked AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY wialon_id ORDER BY pos_t DESC
                ) AS rn
                FROM combined
            )
            SELECT wialon_id, nm, pos_t, pos_y, pos_x, registration_plate
            FROM ranked WHERE rn = 1
        """)
        return [dict(r) for r in rows]

    async def get_avg_speeds(self) -> dict[int, float]:
        """Compute avg speed (km/h) per vehicle from delta between snapshots."""
        rows = await self.pool.fetch(f"""
            WITH s1 AS (
                SELECT wialon_id, pos_t, pos_y, pos_x
                FROM {SCHEMA}.wialon_units_snapshot_1
            ),
            s2 AS (
                SELECT wialon_id, pos_t, pos_y, pos_x
                FROM {SCHEMA}.wialon_units_snapshot_2
            ),
            s3 AS (
                SELECT wialon_id, pos_t, pos_y, pos_x
                FROM {SCHEMA}.wialon_units_snapshot_3
            ),
            pairs AS (
                SELECT s1.wialon_id,
                       s1.pos_t AS t1, s1.pos_y AS y1, s1.pos_x AS x1,
                       s2.pos_t AS t2, s2.pos_y AS y2, s2.pos_x AS x2
                FROM s1 JOIN s2 USING (wialon_id)
                UNION ALL
                SELECT s2.wialon_id,
                       s2.pos_t, s2.pos_y, s2.pos_x,
                       s3.pos_t, s3.pos_y, s3.pos_x
                FROM s2 JOIN s3 USING (wialon_id)
            )
            SELECT wialon_id,
                AVG(
                    CASE WHEN (t2 - t1) > 60 THEN
                        111.32 * SQRT(
                            POWER((y2 - y1), 2) +
                            POWER((x2 - x1) * COS(RADIANS((y1 + y2) / 2)), 2)
                        ) / ((t2 - t1)::float / 3600)
                    ELSE NULL END
                ) AS avg_speed_kmh
            FROM pairs
            GROUP BY wialon_id
        """)
        result: dict[int, float] = {}
        for r in rows:
            spd = r["avg_speed_kmh"]
            if spd and settings.min_speed_kmh < spd < settings.max_speed_kmh:
                result[r["wialon_id"]] = float(spd)
            else:
                result[r["wialon_id"]] = settings.default_speed_kmh
        return result

    # ── Tasks ────────────────────────────────────────────────────────────────

    async def get_task_by_id(self, task_id: str) -> Optional[dict]:
        row = await self.pool.fetchrow(
            """SELECT task_id, priority, planned_start, planned_duration_hours,
                      destination_uwi, task_type, shift, start_day
               FROM tasks WHERE task_id = $1""",
            task_id,
        )
        return dict(row) if row else None

    async def get_tasks_by_ids(self, task_ids: list[str]) -> list[dict]:
        rows = await self.pool.fetch(
            """SELECT task_id, priority, planned_start, planned_duration_hours,
                      destination_uwi, task_type, shift, start_day
               FROM tasks WHERE task_id = ANY($1::text[])""",
            task_ids,
        )
        return [dict(r) for r in rows]

    async def get_active_tasks(self) -> list[dict]:
        rows = await self.pool.fetch("""
            SELECT task_id, priority, planned_start, planned_duration_hours,
                   destination_uwi, task_type, shift, start_day
            FROM tasks
            WHERE planned_start + (planned_duration_hours * interval '1 hour') > NOW()
              AND planned_start <= NOW()
        """)
        return [dict(r) for r in rows]

    async def get_vehicle_busy_until(self) -> dict[int, datetime]:
        """
        §7.3: для каждой единицы техники определяем free_at —
        время окончания последнего активного назначения.
        free_at = planned_start + planned_duration_hours для самой поздней заявки.
        """
        try:
            rows = await self.pool.fetch("""
                SELECT a.wialon_id,
                       MAX(t.planned_start
                           + (t.planned_duration_hours * interval '1 hour')) AS free_at
                FROM public.assignments a
                JOIN public.tasks t ON t.task_id = a.task_id
                WHERE a.is_active = TRUE
                  AND t.planned_start + (t.planned_duration_hours * interval '1 hour') > NOW()
                GROUP BY a.wialon_id
            """)
            return {int(r["wialon_id"]): r["free_at"] for r in rows}
        except Exception:
            # Таблица assignments пуста или не существует — все машины свободны
            return {}

    # ── Vehicle registry ─────────────────────────────────────────────────────

    async def get_vehicle_registry(self) -> dict[int, str]:
        """
        Возвращает {wialon_id: vehicle_type} из таблицы vehicle_registry.
        Нормализованный тип, определённый автоматически из nm или
        скорректированный диспетчером вручную (type_source='manual').
        Используется в FleetManager вместо хрупкого substring-матча по nm.
        """
        try:
            rows = await self.pool.fetch(
                "SELECT wialon_id, vehicle_type FROM public.vehicle_registry"
            )
            return {int(r["wialon_id"]): r["vehicle_type"] for r in rows}
        except Exception:
            # Таблица ещё не создана (миграция 002 не накатана) — возвращаем пустой словарь,
            # FleetManager упадёт на фолбэк substring-матч по nm
            return {}

    # ── Compatibility ────────────────────────────────────────────────────────

    async def get_compatibility(self) -> dict[str, list[str]]:
        try:
            rows = await self.pool.fetch(
                "SELECT task_type, vehicle_types FROM public.compatibility"
            )
            if rows:
                return {r["task_type"]: list(r["vehicle_types"]) for r in rows}
        except Exception:
            pass
        return {}  # таблица пустая или ещё не заполнена — данные загрузятся через make load-hackathon

    # ── Road graph raw ────────────────────────────────────────────────────────

    async def get_road_nodes(self) -> list[dict]:
        rows = await self.pool.fetch(
            f'SELECT node_id, lon, lat FROM {SCHEMA}.road_nodes'
        )
        return [dict(r) for r in rows]

    async def get_road_edges(self) -> list[dict]:
        rows = await self.pool.fetch(
            f'SELECT source, target, weight FROM {SCHEMA}.road_edges'
        )
        return [dict(r) for r in rows]