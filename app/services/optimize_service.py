"""
Optimize Service
================
Оркестрирует VRP-решение (OR-Tools) и greedy baseline.
Изолирует роутер /optimize от деталей решателя.
"""
from __future__ import annotations

import asyncio
import math

from app.core.config import settings
from app.core.exceptions import AlgorithmTimeoutError, ValidationError
from app.core.models import Task, Vehicle
from app.fleet.manager import FleetManager
from app.graph.road_graph import RoadGraph
from app.optimizer.vrp_solver import solve_vrp, greedy_baseline, VRPResult, GreedyResult


def _haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Расстояние по прямой между двумя точками (Haversine), км."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _select_relevant_vehicles(
    vehicles: list[Vehicle],
    tasks: list[Task],
    max_vehicles: int,
    is_compatible_fn,
) -> list[Vehicle]:
    """
    Выбирает наиболее релевантных машин для набора задач:
    1. Исключает занятые машины (is_busy=True)
    2. Исключает машины без совместимых типов работ
    3. Исключает машины дальше vrp_max_distance_km (по прямой — Haversine)
    4. Сортирует по минимальному прямолинейному расстоянию до задач
    5. Берёт топ-N ближайших (не более max_vehicles)
    """
    task_types = {t.task_type for t in tasks if t.task_type}
    max_dist   = settings.vrp_max_distance_km

    scored: list[tuple[float, Vehicle]] = []
    for v in vehicles:
        # Пропускаем занятые машины
        if v.is_busy:
            continue

        # Проверяем совместимость хотя бы с одним типом задач
        if task_types and is_compatible_fn:
            if not any(is_compatible_fn(v, tt) for tt in task_types):
                continue

        # Минимальное прямолинейное расстояние до любой задачи (Haversine)
        min_dist = min(
            _haversine_km(v.current_lon, v.current_lat, t.dest_lon, t.dest_lat)
            for t in tasks
            if t.dest_lon and t.dest_lat
        ) if tasks else 99999.0

        if min_dist > max_dist:
            continue  # машина слишком далеко

        scored.append((min_dist, v))

    # Сортируем по близости, берём топ N
    scored.sort(key=lambda x: x[0])
    selected = [v for _, v in scored[:max_vehicles]]

    if not selected:
        # Фолбэк: если всё отфильтровано — берём ближайших без ограничения дистанции
        import logging
        logging.getLogger(__name__).warning(
            "VRP: все машины отфильтрованы (занятые/далёкие), "
            "fallback — ближайшие %d по прямой из %d", max_vehicles, len(vehicles)
        )
        fallback = sorted(
            (
                (min(
                    _haversine_km(v.current_lon, v.current_lat, t.dest_lon, t.dest_lat)
                    for t in tasks if t.dest_lon and t.dest_lat
                ) if tasks else 99999.0, v)
                for v in vehicles if not v.is_busy
            ),
            key=lambda x: x[0],
        )
        selected = [v for _, v in fallback[:max_vehicles]]
        if not selected:
            selected = vehicles[:max_vehicles]

    return selected


class OptimizeService:
    def __init__(self, graph: RoadGraph, fleet: FleetManager) -> None:
        self.graph = graph
        self.fleet = fleet

    async def solve(
        self,
        tasks: list[Task],
        time_limit_seconds: int = 10,
    ) -> tuple[VRPResult, GreedyResult]:
        """Запускает VRP + greedy, возвращает оба результата."""
        all_vehicles = list(self.fleet.vehicles.values())
        if not all_vehicles:
            raise ValidationError("No vehicles loaded in fleet")

        # Фильтруем технику по Haversine (прямолинейно) — быстро, без графа
        vehicles = _select_relevant_vehicles(
            all_vehicles, tasks, settings.vrp_max_vehicles, self.fleet.is_compatible
        )

        import logging
        log = logging.getLogger(__name__)
        log.info(
            "VRP: всего техники %d, отобрано %d ближайших для %d задач",
            len(all_vehicles), len(vehicles), len(tasks),
        )

        # Матрица строится только по узлам задач.
        # Стартовые узлы техники из снапшота могут быть вне графа задач —
        # включать их создаёт асимметрию между VRP и Greedy.
        # Оба алгоритма стартуют с ближайшего узла задачи (честное сравнение).
        task_nodes = list({t.destination_node for t in tasks})

        try:
            dist_matrix = await asyncio.wait_for(
                asyncio.to_thread(self.graph.distance_matrix, task_nodes, task_nodes),
                timeout=settings.timeout_distance_matrix,
            )
        except asyncio.TimeoutError:
            raise AlgorithmTimeoutError("distance_matrix", settings.timeout_distance_matrix)

        # Переназначаем start_node каждой машины на ближайший узел задачи
        # чтобы VRP и Greedy работали в одном пространстве
        def _snap_to_tasks(v: Vehicle) -> Vehicle:
            if v.start_node in task_nodes:
                return v
            best_node = task_nodes[0]
            best_km = math.inf
            for tn in task_nodes:
                km = dist_matrix.get((v.start_node, tn),
                     dist_matrix.get((tn, v.start_node), math.inf))
                if km < best_km:
                    best_km = km
                    best_node = tn
            from copy import copy as _copy
            vc = _copy(v)
            vc.start_node = best_node
            return vc

        vehicles = [_snap_to_tasks(v) for v in vehicles]

        # VRP
        vrp_result = await asyncio.wait_for(
            asyncio.to_thread(
                solve_vrp,
                tasks, vehicles, dist_matrix,
                time_limit_seconds,
                self.fleet.is_compatible,
            ),
            timeout=time_limit_seconds + 5.0,
        )

        # Greedy baseline
        greedy_result = await asyncio.to_thread(
            greedy_baseline,
            tasks, vehicles, dist_matrix,
            self.fleet.is_compatible,
        )

        return vrp_result, greedy_result

    async def build_dist_matrix(self, tasks: list[Task]) -> dict:
        """Строит матрицу расстояний для simulate endpoint (только релевантная техника)."""
        all_vehicles = list(self.fleet.vehicles.values())

        vehicles = _select_relevant_vehicles(
            all_vehicles, tasks, settings.vrp_max_vehicles, self.fleet.is_compatible
        )

        all_nodes = list(
            {v.start_node for v in vehicles} | {t.destination_node for t in tasks}
        )
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self.graph.distance_matrix, all_nodes, all_nodes),
                timeout=settings.timeout_distance_matrix,
            )
        except asyncio.TimeoutError:
            raise AlgorithmTimeoutError("distance_matrix_sim", settings.timeout_distance_matrix)