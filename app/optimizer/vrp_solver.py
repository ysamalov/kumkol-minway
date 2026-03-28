"""
VRP Solver (VRPTW, Multi-Depot, Open-end routes)
=================================================
Решает задачу Vehicle Routing Problem с OR-Tools.

Ключевые принципы:
  - TaskCount dimension гарантирует равномерное распределение задач.
    cap = ceil(n_tasks / n_vehicles) + 1, минимум 3.
  - Без жёстких SetRange на время — они вызывают инфизабельность
    при длинных задачах и заставляют OR-Tools сваливать всё на одну машину.
  - PARALLEL_CHEAPEST_INSERTION распределяет задачи сразу по всем машинам.
  - Совместимость через INF_COST на дугах depot→task.
  - Приоритеты через penalty за дроп (high=1M, medium=100K, low=10K).
"""
from __future__ import annotations

import math
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

from ortools.constraint_solver import routing_enums_pb2, pywrapcp

from app.core.models import Task, Vehicle, Priority
from app.core.config import settings

log = logging.getLogger(__name__)

INF_COST = 10_000_000


@dataclass
class VRPResult:
    routes: dict[int, list[str]]
    total_distance_km: float
    total_time_minutes: float
    dropped_tasks: list[str]
    solver_status: str
    vehicle_distances: dict[int, float]
    total_idle_minutes: float = 0.0
    vehicle_idle_minutes: dict[int, float] = field(default_factory=dict)


@dataclass
class GreedyResult:
    routes: dict[int, list[str]]
    total_distance_km: float
    total_time_minutes: float
    dropped_tasks: list[str]
    total_idle_minutes: float = 0.0
    vehicle_idle_minutes: dict[int, float] = field(default_factory=dict)


def solve_vrp(
    tasks: list[Task],
    vehicles: list[Vehicle],
    dist_matrix: dict[tuple[int, int], float],
    time_limit_seconds: int = 10,
    is_compatible_fn=None,
) -> VRPResult:
    if not tasks or not vehicles:
        return VRPResult(
            routes={}, total_distance_km=0, total_time_minutes=0,
            dropped_tasks=[t.task_id for t in tasks],
            solver_status="INFEASIBLE", vehicle_distances={},
        )

    nV = len(vehicles)
    nT = len(tasks)
    N  = nV + nT

    # ── 1. Узлы графа ────────────────────────────────────────────────────────
    def graph_node(i: int) -> int:
        return vehicles[i].start_node if i < nV else tasks[i - nV].destination_node

    # ── 2. Скорости ──────────────────────────────────────────────────────────
    avg_spd = sum(v.avg_speed_kmh or settings.default_speed_kmh for v in vehicles) / nV

    def spd(v_idx: int) -> float:
        return vehicles[v_idx].avg_speed_kmh or avg_spd

    # ── 3. Матрицы расстояний и времени (целые числа) ────────────────────────
    dist_m = [[0] * N for _ in range(N)]
    time_m = [[0] * N for _ in range(N)]

    for i in range(N):
        for j in range(N):
            if i == j:
                continue
            ni, nj = graph_node(i), graph_node(j)
            km = dist_matrix.get((ni, nj)) or dist_matrix.get((nj, ni)) or math.inf

            # Блокируем несовместимые пары depot→task
            if i < nV and j >= nV:
                t = tasks[j - nV]
                v = vehicles[i]
                if is_compatible_fn and t.task_type and not is_compatible_fn(v, t.task_type):
                    km = INF_COST / 1000.0

            if km == math.inf:
                km = INF_COST / 1000.0

            dist_m[i][j] = int(km * 1000)
            v_idx = i if i < nV else 0
            time_m[i][j] = int(km / spd(v_idx) * 60)

    # ── 4. Service times ─────────────────────────────────────────────────────
    service_times = [0] * nV + [t.service_minutes for t in tasks]

    # ── 5. Горизонт планирования ─────────────────────────────────────────────
    # Берём максимальную длительность всех задач + время в пути + буфер смены.
    # Это верхняя граница времени для Time dimension.
    total_service = sum(service_times)
    max_single = max(service_times[nV:]) if nT > 0 else 0
    horizon = max(
        total_service + nT * int(INF_COST / 1000.0 / avg_spd * 60),  # верхняя оценка
        settings.shift_hours * 60 * 5,  # минимум 5 смен
        3000,
    )
    # Практичный горизонт: не более 7 дней
    horizon = min(horizon, 7 * 24 * 60)

    # ── 6. OR-Tools модель ───────────────────────────────────────────────────
    manager = pywrapcp.RoutingIndexManager(
        N, nV, list(range(nV)), list(range(nV))
    )
    routing = pywrapcp.RoutingModel(manager)

    def dist_cb(fi, ti):
        return dist_m[manager.IndexToNode(fi)][manager.IndexToNode(ti)]

    def time_cb(fi, ti):
        i = manager.IndexToNode(fi)
        return time_m[i][manager.IndexToNode(ti)] + service_times[i]

    dc_idx = routing.RegisterTransitCallback(dist_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(dc_idx)

    tc_idx = routing.RegisterTransitCallback(time_cb)
    routing.AddDimension(
        tc_idx,
        slack_max=horizon,
        capacity=horizon,
        fix_start_cumul_to_zero=True,
        name="Time",
    )
    # Не используем SetRange — жёсткие TW приводят к инфизабельности
    # при длинных задачах и заставляют OR-Tools сваливать всё на одну машину.

    # ── 7. TaskCount: ограничение числа задач на машину
    # cap = ceil(nT / nV) * 2  — достаточно свободы для оптимизации,
    # но не позволяет одной машине взять всё.
    # Минимум 3 задачи на машину чтобы не блокировать маленькие наборы.
    task_cap = max(3, math.ceil(nT / max(nV, 1)) * 2)

    def unit_cb(from_idx: int) -> int:
        return 0 if manager.IndexToNode(from_idx) < nV else 1

    uc_idx = routing.RegisterUnaryTransitCallback(unit_cb)
    routing.AddDimensionWithVehicleCapacity(
        uc_idx, 0, [task_cap] * nV, True, "TaskCount"
    )
    # Фиксированная стоимость использования каждой машины.
    # Без неё OR-Tools кладёт все задачи на одну машину (минимизирует суммарный пробег).
    # С ней решатель балансирует: выгоднее задействовать близкую машину,
    # чем гнать одну дальнюю через всё поле.
    # Стоимость ≈ средний пробег до задачи / 5 — достаточно чтобы побудить к распределению,
    # но не настолько высоко чтобы OR-Tools дропал задачи.
    all_task_dists = [
        dist_m[v_idx][nV + t_idx]
        for v_idx in range(nV)
        for t_idx in range(nT)
        if dist_m[v_idx][nV + t_idx] < INF_COST
    ]
    avg_dist = int(sum(all_task_dists) / max(len(all_task_dists), 1))
    fixed_cost = max(500, avg_dist // 5)
    for v_idx in range(nV):
        routing.SetFixedCostOfVehicle(fixed_cost, v_idx)

    # ── 8. Приоритеты: штраф за дроп ─────────────────────────────────────────
    penalty_map = {
        Priority.high:   1_000_000,
        Priority.medium: 100_000,
        Priority.low:    10_000,
    }
    for t_idx, t in enumerate(tasks):
        idx = manager.NodeToIndex(nV + t_idx)
        routing.AddDisjunction([idx], penalty_map[t.priority])

    # ── 9. Параметры поиска ───────────────────────────────────────────────────
    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    params.time_limit.seconds = time_limit_seconds
    params.log_search = False

    # ── 10. Решение ───────────────────────────────────────────────────────────
    solution = routing.SolveWithParameters(params)

    if not solution:
        log.warning("OR-Tools VRP: нет решения")
        return VRPResult(
            routes={}, total_distance_km=0, total_time_minutes=0,
            dropped_tasks=[t.task_id for t in tasks],
            solver_status="INFEASIBLE", vehicle_distances={},
        )

    # ── 11. Извлечение маршрутов ─────────────────────────────────────────────
    routes: dict[int, list[str]] = {}
    vehicle_distances: dict[int, float] = {}
    assigned: set[str] = set()
    total_dist_m = 0
    total_time_m = 0

    for v_idx in range(nV):
        v = vehicles[v_idx]
        idx = routing.Start(v_idx)
        route_tasks: list[str] = []
        route_dist = 0

        while not routing.IsEnd(idx):
            node = manager.IndexToNode(idx)
            if node >= nV:
                t = tasks[node - nV]
                route_tasks.append(t.task_id)
                assigned.add(t.task_id)
            nxt = solution.Value(routing.NextVar(idx))
            route_dist += routing.GetArcCostForVehicle(idx, nxt, v_idx)
            idx = nxt

        if route_tasks:
            routes[v.vehicle_id] = route_tasks
            dist_km = route_dist / 1000.0
            vehicle_distances[v.vehicle_id] = round(dist_km, 2)
            total_dist_m += route_dist
            total_time_m += int(dist_km / (v.avg_speed_kmh or settings.default_speed_kmh) * 60)
            for tid in route_tasks:
                t = next(x for x in tasks if x.task_id == tid)
                total_time_m += t.service_minutes

    dropped = [t.task_id for t in tasks if t.task_id not in assigned]

    status_map = {0: "NOT_SOLVED", 1: "OPTIMAL", 2: "FAIL", 3: "TIMEOUT", 4: "INVALID"}
    log.info(
        "VRP: %d машин, %d назначено, %d дропнуто, cap=%d, статус=%s",
        len(routes), len(assigned), len(dropped), task_cap,
        status_map.get(routing.status(), "?"),
    )

    return VRPResult(
        routes=routes,
        total_distance_km=round(total_dist_m / 1000.0, 2),
        total_time_minutes=round(total_time_m, 1),
        dropped_tasks=dropped,
        solver_status=status_map.get(routing.status(), "FEASIBLE"),
        vehicle_distances=vehicle_distances,
    )


# ── Greedy baseline ───────────────────────────────────────────────────────────

def greedy_baseline(
    tasks: list[Task],
    vehicles: list[Vehicle],
    dist_matrix: dict[tuple[int, int], float],
    is_compatible_fn=None,
) -> GreedyResult:
    """
    Naive greedy: для каждой задачи назначает ближайшую свободную совместимую машину.
    Используется как baseline для сравнения с VRP.
    """
    assignment: dict[int, list[str]] = {v.vehicle_id: [] for v in vehicles}
    current_node: dict[int, int] = {v.vehicle_id: v.start_node for v in vehicles}
    finish_at: dict[int, float] = {v.vehicle_id: 0.0 for v in vehicles}
    idle_minutes: dict[int, float] = {v.vehicle_id: 0.0 for v in vehicles}
    dropped: list[str] = []
    total_dist = 0.0
    total_time = 0.0

    for task in tasks:
        best_vid  = -1
        best_cost = math.inf

        for v in vehicles:
            if is_compatible_fn and not is_compatible_fn(v, task.task_type):
                continue
            src = current_node[v.vehicle_id]
            dst = task.destination_node
            km  = dist_matrix.get((src, dst), dist_matrix.get((dst, src), math.inf))
            if km == math.inf:
                continue
            spd_v = v.avg_speed_kmh or settings.default_speed_kmh
            # cost = пробег км + эквивалент ожидания в км
            wait_min = finish_at[v.vehicle_id]  # минуты до освобождения
            cost = km + wait_min * spd_v / 60.0
            if cost < best_cost:
                best_cost = cost
                best_vid  = v.vehicle_id

        if best_vid < 0:
            dropped.append(task.task_id)
            continue

        assignment[best_vid].append(task.task_id)
        v_obj = next(x for x in vehicles if x.vehicle_id == best_vid)
        spd = v_obj.avg_speed_kmh or 40.0
        km = dist_matrix.get(
            (current_node[best_vid], task.destination_node),
            dist_matrix.get((task.destination_node, current_node[best_vid]), 0.0)
        )
        travel_min = (km / spd) * 60.0
        prev_finish = finish_at[best_vid]
        # idle = время пока машина ждёт следующую задачу (finish_at уже в минутах)
        idle_gap = max(0.0, prev_finish - travel_min) if prev_finish > 0 else 0.0
        idle_minutes[best_vid] += idle_gap

        current_node[best_vid] = task.destination_node
        # finish_at накапливается в минутах от начала смены
        finish_at[best_vid] = prev_finish + travel_min + task.service_minutes
        total_dist += km
        total_time += travel_min + task.service_minutes

    routes = {vid: tids for vid, tids in assignment.items() if tids}
    total_idle = sum(idle_minutes.values())

    return GreedyResult(
        routes=routes,
        total_distance_km=round(total_dist, 2),
        total_time_minutes=round(total_time, 1),
        dropped_tasks=dropped,
        total_idle_minutes=round(total_idle, 1),
        vehicle_idle_minutes={vid: round(v, 1) for vid, v in idle_minutes.items()},
    )