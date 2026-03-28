"""
Task Service
============
Бизнес-логика загрузки и преобразования заявок из БД в доменные объекты.
Убирает дублирование кода из роутеров /multitask, /optimize, /simulate.
"""
from __future__ import annotations

from datetime import date, datetime, timezone, timedelta

from app.core.exceptions import NotFoundError, ValidationError
from app.core.models import Task, Priority, Shift, compute_end_day, SHIFT_HOURS
from app.db.repository import Repository
from app.graph.road_graph import RoadGraph
from app.core.config import settings



def _shift_window(start_day: date, shift: Shift):
    base = datetime(start_day.year, start_day.month, start_day.day, tzinfo=timezone.utc)
    if shift == Shift.day:
        return base.replace(hour=8), base.replace(hour=20)
    return base.replace(hour=20), (base + timedelta(days=1)).replace(hour=8)


class TaskService:
    def __init__(self, repo: Repository, graph: RoadGraph) -> None:
        self.repo = repo
        self.graph = graph

    async def load_tasks_by_ids(self, task_ids: list[str]) -> list[Task]:
        """Загружает список заявок из БД и собирает доменные объекты Task."""
        if not task_ids:
            raise ValidationError("task_ids cannot be empty")
        # Лимит проверяется на уровне Pydantic-моделей (ValidatedMultitaskRequest /
        # ValidatedOptimizeRequest) — здесь используем max_vrp_tasks как общий потолок
        limit = settings.max_vrp_tasks
        if len(task_ids) > limit:
            raise ValidationError(
                f"Too many tasks: {len(task_ids)}. Maximum is {limit}."
            )

        raw = await self.repo.get_tasks_by_ids(task_ids)
        if not raw:
            raise NotFoundError("Tasks", str(task_ids))

        tasks: list[Task] = []
        for rt in raw:
            task = await self._build_task(rt)
            if task:
                tasks.append(task)

        if not tasks:
            raise NotFoundError("Tasks", str(task_ids))

        return tasks

    async def load_tasks_in_window(
        self, horizon_start: datetime, horizon_end: datetime
    ) -> list[Task]:
        """Загружает заявки в заданном временном окне."""
        rows = await self.repo.pool.fetch(
            """SELECT task_id, priority, planned_start, planned_duration_hours,
                      destination_uwi, task_type, shift, start_day
               FROM tasks
               WHERE planned_start >= $1 AND planned_start <= $2
               ORDER BY planned_start""",
            horizon_start, horizon_end,
        )

        tasks: list[Task] = []
        for rt in rows:
            task = await self._build_task(dict(rt))
            if task:
                tasks.append(task)

        return tasks

    async def build_task_from_request(
        self,
        task_id: str,
        priority: Priority,
        destination_uwi: str,
        planned_start: datetime,
        duration_hours: float,
        task_type: str = "",
    ) -> Task:
        """Собирает Task из параметров запроса (для /recommendations)."""
        well = await self.repo.get_well_by_uwi(destination_uwi)
        if not well:
            raise NotFoundError("Well", destination_uwi)

        dest_node = self.graph.snap_to_node(well["longitude"], well["latitude"])

        hour = planned_start.hour
        shift = Shift.night if (hour >= 20 or hour < 8) else Shift.day
        tw_start, tw_end = _shift_window(planned_start.date(), shift)
        end_day = compute_end_day(planned_start.date(), duration_hours)

        return Task(
            task_id=task_id,
            priority=priority,
            planned_start=planned_start,
            planned_duration_hours=duration_hours,
            destination_uwi=destination_uwi,
            task_type=task_type,
            shift=shift,
            start_day=planned_start.date(),
            end_day=end_day,
            destination_node=dest_node,
            dest_lon=well["longitude"],
            dest_lat=well["latitude"],
            tw_start=tw_start,
            tw_end=tw_end,
            service_minutes=int(duration_hours * 60),
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _build_task(self, rt: dict) -> Task | None:
        """Собирает Task из строки БД. Возвращает None если скважина не найдена."""
        well = await self.repo.get_well_by_uwi(rt["destination_uwi"])
        if not well:
            return None

        dest_node = self.graph.snap_to_node(well["longitude"], well["latitude"])
        shift = Shift(rt["shift"]) if isinstance(rt["shift"], str) else rt["shift"]
        start_day = rt["start_day"]
        if isinstance(start_day, datetime):
            start_day = start_day.date()

        tw_start, tw_end = _shift_window(start_day, shift)
        duration_hours = float(rt["planned_duration_hours"])

        return Task(
            task_id=rt["task_id"],
            priority=Priority(rt["priority"]),
            planned_start=rt["planned_start"],
            planned_duration_hours=duration_hours,
            destination_uwi=rt["destination_uwi"],
            task_type=rt["task_type"],
            shift=shift,
            start_day=start_day,
            end_day=compute_end_day(start_day, duration_hours),
            destination_node=dest_node,
            dest_lon=well["longitude"],
            dest_lat=well["latitude"],
            tw_start=tw_start,
            tw_end=tw_end,
            service_minutes=int(duration_hours * 60),
        )