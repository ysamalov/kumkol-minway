"""
24-Hour Event Simulator
=======================
Simulates task execution over a time horizon using event-driven assignment.

Two modes:
  - baseline:   greedy nearest-free-compatible vehicle (no look-ahead)
  - optimized:  priority-weighted scoring with wait penalty (same as scorer.py)

Algorithm:
  for task in tasks_sorted_by_planned_start:
      vehicle = choose_best_vehicle(task)
      travel_time = dist(vehicle.node, task.node) / speed
      start_time  = max(vehicle.free_at, task.planned_start)
      finish_time = start_time + travel_time + task.service_minutes
      vehicle.free_at = finish_time
      vehicle.node    = task.destination_node
      record(assignment, is_late)
"""
from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.core.models import Task, Vehicle, Priority


from app.core.config import settings

def _sla_hours() -> dict:
    return {
        Priority.high:   settings.sla_high_hours,
        Priority.medium: settings.sla_medium_hours,
        Priority.low:    settings.sla_low_hours,
    }


@dataclass
class VehicleState:
    vehicle_id: int
    name: str
    node: int
    free_at: datetime          # when the vehicle becomes available
    total_distance_km: float = 0.0
    assigned_tasks: list[str] = field(default_factory=list)
    total_idle_minutes: float = 0.0   # суммарный простой за горизонт


@dataclass
class TaskResult:
    task_id: str
    priority: str
    vehicle_id: int
    vehicle_name: str
    planned_start: datetime
    actual_start: datetime
    finish_time: datetime
    travel_km: float
    is_late: bool
    delay_minutes: float


@dataclass
class SimResult:
    assignments: list[TaskResult]
    unassigned: list[str]
    total_distance_km: float
    late_tasks: int
    total_tasks: int
    utilization: dict[int, float]          # vehicle_id -> busy_minutes
    total_idle_minutes: float = 0.0        # суммарный простой всех машин
    vehicle_idle_minutes: dict[int, float] = field(default_factory=dict)  # per vehicle


def _travel(src: int, dst: int,
            dist_matrix: dict[tuple[int, int], float],
            speed_kmh: float) -> tuple[float, float]:
    """Returns (distance_km, travel_minutes)."""
    km = dist_matrix.get((src, dst), dist_matrix.get((dst, src), math.inf))
    if km == math.inf or km == 0:
        km = 0.0
    minutes = (km / speed_kmh) * 60.0 if speed_kmh > 0 else 0.0
    return km, minutes


_SHIFT_START_UTC = timedelta(hours=3)  # 08:00 Астана = 03:00 UTC


def _effective_start(task) -> datetime:
    """Если planned_start = полночь UTC (дата без времени из БД),
    сдвигаем на начало дневной смены 08:00 Астана = 03:00 UTC."""
    ps = task.planned_start
    if ps and ps.hour == 0 and ps.minute == 0 and ps.second == 0:
        return ps + _SHIFT_START_UTC
    return ps


def simulate(
    tasks: list[Task],
    vehicles: list[Vehicle],
    dist_matrix: dict[tuple[int, int], float],
    horizon_start: datetime,
    horizon_hours: int = 24,
    mode: str = "greedy",          # "greedy" | "optimized"
    is_compatible_fn=None,
) -> SimResult:
    """
    Run event simulation over [horizon_start, horizon_start + horizon_hours].

    mode="greedy"    — baseline: pick nearest free compatible vehicle
    mode="optimized" — scored:   balance distance, wait, priority
    """
    horizon_end = horizon_start + timedelta(hours=horizon_hours)

    # Filter tasks within horizon
    window_tasks = [
        t for t in tasks
        if t.planned_start and horizon_start <= _effective_start(t) <= horizon_end
    ]
    window_tasks.sort(key=lambda t: _effective_start(t))

    # Build mutable vehicle states.
    # free_at = horizon_start (симуляция не знает реальной занятости).
    # start_node: если реальная позиция машины не входит в dist_matrix
    # (например, тестовые задачи 2025г. и реальные снапшоты 2026г.),
    # используем ближайший узел из задач горизонта — иначе все расстояния inf.
    task_nodes_in_matrix = {t.destination_node for t in window_tasks}

    def _nearest_task_node(v: "Vehicle") -> int:
        """Если start_node машины не в матрице — берём ближайший узел из задач."""
        if v.start_node in task_nodes_in_matrix:
            return v.start_node
        best_node = v.start_node
        best_km = math.inf
        for tn in task_nodes_in_matrix:
            km = dist_matrix.get((v.start_node, tn),
                 dist_matrix.get((tn, v.start_node), math.inf))
            if km < best_km:
                best_km = km
                best_node = tn
        # Если вообще нет пути — стартуем прямо с первой задачи горизонта
        if best_km == math.inf and window_tasks:
            best_node = window_tasks[0].destination_node
        return best_node

    states: dict[int, VehicleState] = {}
    for v in vehicles:
        states[v.vehicle_id] = VehicleState(
            vehicle_id=v.vehicle_id,
            name=v.name,
            node=_nearest_task_node(v),
            free_at=horizon_start,
        )

    avg_speed = sum(v.avg_speed_kmh or settings.default_speed_kmh for v in vehicles) / max(len(vehicles), 1)

    assignments: list[TaskResult] = []
    unassigned: list[str] = []

    for task in window_tasks:
        best_vid = _pick_vehicle(
            task, states, vehicles, dist_matrix, avg_speed,
            mode, is_compatible_fn,
        )

        if best_vid is None:
            unassigned.append(task.task_id)
            continue

        vs = states[best_vid]
        v_obj = next(x for x in vehicles if x.vehicle_id == best_vid)
        spd = v_obj.avg_speed_kmh or avg_speed

        km, travel_min = _travel(vs.node, task.destination_node, dist_matrix, spd)

        # Vehicle can leave only when free; task can start only at planned_start
        # Используем эффективное время старта (с учётом сдвига полночи на начало смены)
        depart_time  = max(vs.free_at, _effective_start(task))
        actual_start = depart_time + timedelta(minutes=travel_min)
        finish_time  = actual_start + timedelta(minutes=task.service_minutes)

        # SLA check
        deadline = _effective_start(task) + timedelta(hours=_sla_hours().get(task.priority, settings.sla_low_hours))
        is_late = actual_start > deadline
        delay_min = max(0.0, (actual_start - deadline).total_seconds() / 60.0)

        # Idle: если машина освободилась раньше, чем пришло время ехать,
        # она стоит от vs.free_at до depart_time — это и есть простой.
        idle_gap = max(0.0, (depart_time - vs.free_at).total_seconds() / 60.0)
        vs.total_idle_minutes += idle_gap

        # Update state
        vs.node = task.destination_node
        vs.free_at = finish_time
        vs.total_distance_km += km
        vs.assigned_tasks.append(task.task_id)

        assignments.append(TaskResult(
            task_id=task.task_id,
            priority=task.priority.value if hasattr(task.priority, 'value') else str(task.priority),
            vehicle_id=best_vid,
            vehicle_name=vs.name,
            planned_start=_effective_start(task),
            actual_start=actual_start,
            finish_time=finish_time,
            travel_km=round(km, 2),
            is_late=is_late,
            delay_minutes=round(delay_min, 1),
        ))

    total_dist = sum(vs.total_distance_km for vs in states.values())
    late_count = sum(1 for a in assignments if a.is_late)

    # Utilization: busy_minutes per vehicle (travel + service)
    util = {}
    vehicle_idle: dict[int, float] = {}
    for vs in states.values():
        busy = sum(
            (a.finish_time - a.actual_start).total_seconds() / 60.0
            for a in assignments if a.vehicle_id == vs.vehicle_id
        )
        util[vs.vehicle_id] = round(busy, 1)
        vehicle_idle[vs.vehicle_id] = round(vs.total_idle_minutes, 1)

    total_idle = sum(vs.total_idle_minutes for vs in states.values())

    return SimResult(
        assignments=assignments,
        unassigned=unassigned,
        total_distance_km=round(total_dist, 2),
        late_tasks=late_count,
        total_tasks=len(window_tasks),
        utilization=util,
        total_idle_minutes=round(total_idle, 1),
        vehicle_idle_minutes=vehicle_idle,
    )


def _pick_vehicle(
    task: Task,
    states: dict[int, VehicleState],
    vehicles: list[Vehicle],
    dist_matrix: dict[tuple[int, int], float],
    avg_speed: float,
    mode: str,
    is_compatible_fn,
) -> Optional[int]:
    best_vid = None
    best_score = math.inf   # lower is better

    for v in vehicles:
        vs = states[v.vehicle_id]
        if is_compatible_fn and not is_compatible_fn(v, task.task_type):
            continue

        km, travel_min = _travel(vs.node, task.destination_node, dist_matrix, v.avg_speed_kmh or avg_speed)
        if km == math.inf:
            continue

        wait_min = max(0.0, (vs.free_at - _effective_start(task)).total_seconds() / 60.0)

        if mode == "greedy":
            # Baseline: cost = distance + wait-equivalent-distance
            cost = km + wait_min * (v.avg_speed_kmh or avg_speed) / 60.0
        else:
            # Optimized: weighted score (lower = better)
            sla_h = _sla_hours().get(task.priority, settings.sla_low_hours)
            dist_penalty  = min(km / settings.score_dist_reference_km, 1.0)
            wait_penalty  = min(wait_min / (sla_h * 60 + 1), 1.0)
            sla_h = settings.sla_high_hours
            prio_mult = {Priority.high: 1.0, Priority.medium: round(sla_h/settings.sla_medium_hours,3), Priority.low: round(sla_h/settings.sla_low_hours,3)}.get(task.priority, 0.5)
            total_arr = travel_min + wait_min
            prio_penalty = prio_mult * min((total_arr - sla_h * 60) / (sla_h * 60 + 1), 1.0) if total_arr > sla_h * 60 else 0.0
            cost = settings.score_w_dist * dist_penalty + settings.score_w_wait * wait_penalty + settings.score_w_prio * prio_penalty

        if cost < best_score:
            best_score = cost
            best_vid = v.vehicle_id

    return best_vid