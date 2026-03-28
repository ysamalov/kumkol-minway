from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.core.config import settings
from app.core.models import Vehicle
from app.db.repository import Repository
from app.graph.road_graph import RoadGraph

log = logging.getLogger(__name__)


class FleetManager:
    """
    Loads and maintains state of all vehicles from Wialon snapshots.
    """

    def __init__(self, repo: Repository, graph: RoadGraph) -> None:
        self.repo = repo
        self.graph = graph
        self.vehicles: dict[int, Vehicle] = {}
        self.compatibility: dict[str, list[str]] = {}
        self._registry: dict[int, str] = {}

    async def load(self) -> None:
        snaps = await self.repo.get_latest_snapshots()
        speeds = await self.repo.get_avg_speeds()
        self.compatibility = await self.repo.get_compatibility()
        self._registry = await self.repo.get_vehicle_registry()
        active_tasks = await self.repo.get_active_tasks()

        busy_until: dict[int, datetime] = await self.repo.get_vehicle_busy_until()
        _ = active_tasks

        for s in snaps:
            vid = s["wialon_id"]
            lon, lat = float(s["pos_x"]), float(s["pos_y"])
            start_node = self.graph.snap_to_node(lon, lat)

            spd = speeds.get(vid, settings.default_speed_kmh)
            free_at = busy_until.get(vid, datetime.now(timezone.utc))
            is_busy = free_at > datetime.now(timezone.utc)

            self.vehicles[vid] = Vehicle(
                vehicle_id=vid,
                name=s["nm"] or "",
                reg_plate=s.get("registration_plate") or "",
                start_node=start_node,
                current_lon=lon,
                current_lat=lat,
                pos_timestamp=int(s["pos_t"]),
                free_at=free_at,
                avg_speed_kmh=spd,
                skills=self._skills_for(vid, s["nm"] or ""),
                is_busy=is_busy,
            )

    def _skills_for(self, wialon_id: int, vehicle_name: str) -> list[str]:
        """
        Определяем какие типы работ может выполнять машина.
        Логика: vehicle_type из реестра ищем в списке паттернов таблицы compatibility.
        Если совпадений нет — машина может выполнять все виды работ (fallback).
        """
        all_task_types = [t for t in self.compatibility if t != "default"]
        vehicle_type = self._registry.get(wialon_id, "UNKNOWN")

        # Ищем совместимые задачи через vehicle_type из реестра
        if vehicle_type and vehicle_type != "UNKNOWN":
            skills = []
            for task_type, patterns in self.compatibility.items():
                if task_type == "default":
                    continue
                if any(
                    p.upper() == vehicle_type.upper()  # точное совпадение типа
                    for p in patterns
                ):
                    skills.append(task_type)
            if skills:
                return skills
            # Тип известен но не в таблице совместимости — машина ничего не делает
            # (лучше не назначать, чем назначить неправильно)
            log.warning(
                "vehicle wialon_id=%s type=%r — тип не найден в compatibility. "
                "Машина не будет назначаться на задачи.",
                wialon_id, vehicle_type,
            )
            return []

        # vehicle_type == UNKNOWN — пробуем по имени
        name_upper = vehicle_name.upper()
        skills = []
        for task_type, patterns in self.compatibility.items():
            if task_type == "default":
                continue
            if any(p.upper() in name_upper for p in patterns):
                skills.append(task_type)
        if skills:
            return skills

        # Полностью неизвестная машина
        log.warning(
            "vehicle wialon_id=%s type=UNKNOWN nm=%r — не определён тип. "
            "Назначены только прочие работы.",
            wialon_id, vehicle_name,
        )
        if settings.strict_compatibility:
            return [t for t in self.compatibility if t == 'Прочие виды работ']
        return all_task_types

    def is_compatible(self, vehicle: Vehicle, task_type: str) -> bool:
        if not task_type:
            return True
        return task_type in vehicle.skills

    def wait_minutes(self, vehicle: Vehicle) -> float:
        """Минуты до освобождения машины от текущего задания.
        Возвращает 0 если машина свободна или её занятость истекла."""
        now = datetime.now(timezone.utc)
        if vehicle.free_at > now:
            diff = (vehicle.free_at - now).total_seconds() / 60.0
            # Ограничиваем одной сменой: если free_at далеко в будущем
            # (например, машина получила задание на демо-дату),
            # не показываем абсурдные значения
            return min(diff, 720.0)
        return 0.0