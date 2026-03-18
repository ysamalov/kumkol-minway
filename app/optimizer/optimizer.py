"""
Optimization core (§7.4)
========================
Supports three modes:
  (a) recommend  — rank vehicles for a single task
  (b) group_tasks — decide multi-stop grouping for a set of tasks
  (c) greedy_baseline — naive nearest-free-compatible assignment for comparison

Изменения vs оригинал:
  - Убран импорт itertools.permutations (не использовался)
  - _greedy_tsp улучшен 2-opt для больших групп
  - Добавлен timeout guard через max_iterations
"""
from __future__ import annotations

import math

from app.core.config import settings
from app.core.models import Task, Vehicle, VehicleCandidate, RouteResult
from app.fleet.manager import FleetManager
from app.graph.road_graph import RoadGraph
from app.scoring.scorer import compute_score


class Optimizer:
    def __init__(self, graph: RoadGraph, fleet: FleetManager) -> None:
        self.graph = graph
        self.fleet = fleet

    # ── (a) Recommendations ──────────────────────────────────────────────────

    def recommend(self, task: Task, top_n: int = 3) -> list[VehicleCandidate]:
        candidates: list[VehicleCandidate] = []

        for vehicle in self.fleet.vehicles.values():
            try:
                route = self.graph.shortest_path(vehicle.start_node, task.destination_node)
            except ValueError:
                continue  # unreachable node — skip

            spd = vehicle.avg_speed_kmh or settings.default_speed_kmh
            eta = (route.distance_km / spd) * 60.0
            route.time_minutes = round(eta, 1)

            wait = self.fleet.wait_minutes(vehicle)
            is_compat = self.fleet.is_compatible(vehicle, task.task_type)

            # Idle: если машина свободна (wait=0), но задача запланирована в будущем,
            # машина будет простаивать от сейчас до planned_start
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            if task.planned_start and task.planned_start > now and wait == 0:
                idle = (task.planned_start - now).total_seconds() / 60.0
            else:
                idle = 0.0

            sc = compute_score(
                vehicle=vehicle,
                task=task,
                distance_km=route.distance_km,
                eta_minutes=eta,
                wait_minutes=wait,
                is_compatible=is_compat,
                idle_minutes=idle,
            )

            candidates.append(VehicleCandidate(
                wialon_id=vehicle.vehicle_id,
                name=vehicle.name,
                eta_minutes=round(eta, 1),
                distance_km=round(route.distance_km, 1),
                score=sc.score,
                reason=sc.reason,
                route=route,
            ))

        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[:top_n]

    # ── (b) Multi-task grouping ───────────────────────────────────────────────

    def group_tasks(
        self,
        tasks: list[Task],
        max_detour_ratio: float = None,
        max_total_time_min: int = None,
    ) -> dict:
        n = len(tasks)
        if max_detour_ratio is None:
            max_detour_ratio = settings.multitask_max_detour_ratio
        if max_total_time_min is None:
            max_total_time_min = settings.multitask_max_time_minutes
        avg_spd = settings.default_speed_kmh

        dest_nodes = [t.destination_node for t in tasks]
        pair_dist = self.graph.distance_matrix(dest_nodes, dest_nodes)

        def d(i: int, j: int) -> float:
            return pair_dist.get((dest_nodes[i], dest_nodes[j]), math.inf)

        # Baseline
        baseline_dist = 0.0
        baseline_time = 0.0
        nearest_v_dist: list[float] = []
        for t in tasks:
            best = math.inf
            for v in self.fleet.vehicles.values():
                try:
                    r = self.graph.shortest_path(v.start_node, t.destination_node)
                    if r.distance_km < best:
                        best = r.distance_km
                except ValueError:
                    pass
            nd = best if best != math.inf else 0.0
            nearest_v_dist.append(nd)
            baseline_dist += nd
            baseline_time += (nd / avg_spd) * 60 + t.service_minutes

        # Union-Find
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            pa, pb = find(a), find(b)
            if pa != pb:
                parent[pa] = pb

        merged_reasons: list[str] = []
        pairs = sorted(
            [(i, j) for i in range(n) for j in range(i + 1, n)],
            key=lambda p: d(p[0], p[1]),
        )

        # Максимальный размер группы: не более 5 заявок на одну машину
        MAX_GROUP_SIZE = settings.vrp_max_group_size

        def group_size(i: int) -> int:
            root = find(i)
            return sum(1 for k in range(n) if find(k) == root)

        for i, j in pairs:
            dij = d(i, j)
            if dij == math.inf:
                continue
            # Не объединяем если группа уже слишком большая
            if group_size(i) >= MAX_GROUP_SIZE or group_size(j) >= MAX_GROUP_SIZE:
                continue
            nd_i = nearest_v_dist[i]
            nd_j = nearest_v_dist[j]
            # Детур: насколько длиннее маршрут при объединении vs раздельно
            # Базовая дистанция — расстояние от ближайшей машины до ближайшей заявки пары
            direct = max(min(nd_i, nd_j), 1.0)  # ближайшая из двух заявок
            detour_dist = min(nd_i, nd_j) + dij  # подъезд + переезд между ними
            ratio = detour_dist / direct
            if ratio > max_detour_ratio:
                continue
            travel_time = (detour_dist / avg_spd) * 60
            svc_time = tasks[i].service_minutes + tasks[j].service_minutes
            if max_total_time_min > 0 and travel_time + svc_time > max_total_time_min:
                continue
            union(i, j)
            merged_reasons.append(
                f"заявки {tasks[i].task_id} и {tasks[j].task_id} "
                f"в {dij:.1f} км (крюк {(ratio - 1) * 100:.0f}%)"
            )

        # Build groups
        groups_map: dict[int, list[int]] = {}
        for i in range(n):
            r = find(i)
            groups_map.setdefault(r, []).append(i)

        groups: list[list[str]] = [
            [tasks[i].task_id for i in idxs]
            for idxs in groups_map.values()
        ]

        # Compute grouped cost with 2-opt improved TSP
        total_dist = 0.0
        total_time = 0.0
        for idxs in groups_map.values():
            if len(idxs) == 1:
                nd = nearest_v_dist[idxs[0]]
                total_dist += nd
                total_time += (nd / avg_spd) * 60 + tasks[idxs[0]].service_minutes
            else:
                order = _tsp_2opt(idxs, lambda a, b: d(a, b))
                nd = nearest_v_dist[order[0]]
                hop = sum(d(order[k], order[k + 1]) for k in range(len(order) - 1))
                dist = nd + hop
                time = (dist / avg_spd) * 60 + sum(tasks[i].service_minutes for i in idxs)
                total_dist += dist
                total_time += time

        savings = 0.0
        if baseline_dist > 0:
            savings = round((1 - total_dist / baseline_dist) * 100, 1)

        n_groups = len(groups)
        if n_groups == 1:
            strategy = "single_unit"
        elif n_groups == n:
            strategy = "separate"
        else:
            strategy = "mixed"

        reason = _group_reason(strategy, groups, tasks, merged_reasons, savings)

        # Assign nearest available vehicle to each group
        # Filter by max distance (same threshold as VRP) and busy status
        def _hav(lon1, lat1, lon2, lat2) -> float:
            R = 6371.0
            dlat = math.radians(lat2 - lat1)
            dlon = math.radians(lon2 - lon1)
            a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
            return R * 2 * math.asin(math.sqrt(a))

        max_dist_km = settings.vrp_max_distance_km
        group_vehicles = []
        used_vehicles: set = set()
        for idxs in groups_map.values():
            best_vid = None
            best_dist = math.inf
            anchor_task = tasks[idxs[0]]
            anchor_node = anchor_task.destination_node
            anchor_lon  = anchor_task.dest_lon
            anchor_lat  = anchor_task.dest_lat

            candidates = []
            for v in self.fleet.vehicles.values():
                if v.vehicle_id in used_vehicles:
                    continue
                if v.is_busy:
                    continue
                # Haversine pre-filter: skip vehicles too far away
                if anchor_lon and anchor_lat:
                    straight = _hav(v.current_lon, v.current_lat, anchor_lon, anchor_lat)
                    if straight > max_dist_km:
                        continue
                candidates.append(v)

            # If all filtered out, fall back to any non-busy vehicle
            if not candidates:
                candidates = [v for v in self.fleet.vehicles.values()
                               if v.vehicle_id not in used_vehicles and not v.is_busy]

            for v in candidates:
                try:
                    r = self.graph.shortest_path(v.start_node, anchor_node)
                    if r.distance_km < best_dist:
                        best_dist = r.distance_km
                        best_vid = v.vehicle_id
                except ValueError:
                    pass

            if best_vid is not None:
                used_vehicles.add(best_vid)
            group_vehicles.append(best_vid)

        return {
            "groups": groups,
            "group_vehicles": group_vehicles,
            "strategy_summary": strategy,
            "total_distance_km": round(total_dist, 1),
            "total_time_minutes": round(total_time, 1),
            "baseline_distance_km": round(baseline_dist, 1),
            "baseline_time_minutes": round(baseline_time, 1),
            "savings_percent": savings,
            "reason": reason,
        }

    # ── (c) Greedy baseline ───────────────────────────────────────────────────

    def greedy_baseline(self, tasks: list[Task]) -> dict[str, int]:
        """Naive: assign nearest free compatible vehicle to each task in order."""
        assignment: dict[str, int] = {}
        used: set[int] = set()

        for task in tasks:
            best_dist = math.inf
            best_vid = -1
            for v in self.fleet.vehicles.values():
                if v.vehicle_id in used:
                    continue
                if not self.fleet.is_compatible(v, task.task_type):
                    continue
                try:
                    r = self.graph.shortest_path(v.start_node, task.destination_node)
                except ValueError:
                    continue
                wait_equiv = self.fleet.wait_minutes(v) * (v.avg_speed_kmh / 60.0)
                total = r.distance_km + wait_equiv
                if total < best_dist:
                    best_dist = total
                    best_vid = v.vehicle_id
            if best_vid >= 0:
                assignment[task.task_id] = best_vid
                used.add(best_vid)

        return assignment


# ── TSP helpers ───────────────────────────────────────────────────────────────

def _greedy_tsp(idxs: list[int], dist_fn) -> list[int]:
    """Nearest-neighbour TSP — O(n²)."""
    visited = {idxs[0]}
    route = [idxs[0]]
    while len(route) < len(idxs):
        cur = route[-1]
        nxt = min(
            (i for i in idxs if i not in visited),
            key=lambda i: dist_fn(cur, i),
        )
        route.append(nxt)
        visited.add(nxt)
    return route


def _tsp_2opt(idxs: list[int], dist_fn, max_iter: int = 100) -> list[int]:
    """
    Greedy TSP улучшенный 2-opt локальным поиском.
    Для n ≤ 4 возвращает greedy без улучшений (нецелесообразно).
    max_iter — защита от зависания на больших группах.
    """
    route = _greedy_tsp(idxs, dist_fn)
    n = len(route)
    if n <= 3:
        return route

    def total_dist(r: list[int]) -> float:
        return sum(dist_fn(r[i], r[i + 1]) for i in range(len(r) - 1))

    improved = True
    iterations = 0
    best_dist = total_dist(route)

    while improved and iterations < max_iter:
        improved = False
        iterations += 1
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                new_route = route[:i] + route[i:j + 1][::-1] + route[j + 1:]
                new_dist = total_dist(new_route)
                if new_dist < best_dist - 1e-9:
                    route = new_route
                    best_dist = new_dist
                    improved = True
                    break
            if improved:
                break

    return route


def _group_reason(
    strategy: str,
    groups: list[list[str]],
    tasks: list[Task],
    merged_reasons: list[str],
    savings: float,
) -> str:
    if strategy == "single_unit":
        return (
            f"все {len(tasks)} заявок объединены в один выезд; "
            f"экономия {savings:.1f}%"
        )
    if strategy == "separate":
        return (
            "заявки расположены в разных частях месторождения; "
            "объединение превышает допустимый крюк — раздельное обслуживание оптимально"
        )
    reason = "; ".join(merged_reasons[:2])
    if len(merged_reasons) > 2:
        reason += f" (+ещё {len(merged_reasons) - 2} объединений)"
    reason += f"; суммарная экономия {savings:.1f}%"
    return reason