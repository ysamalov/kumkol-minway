from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

# §4.5: продолжительность каждой смены в часах
# SHIFT_HOURS берётся из settings.shift_hours в runtime
SHIFT_HOURS = 12  # fallback default

def _get_shift_hours() -> int:
    from app.core.config import settings
    return settings.shift_hours


def compute_end_day(start_day: date, planned_duration_hours: float) -> date:
    """
    end_day = start_day + ceil(planned_duration_hours / SHIFT_HOURS) - 1
    Минимум = start_day (если работа умещается в одну смену).
    """
    spans = math.ceil(planned_duration_hours / _get_shift_hours())
    return start_day + timedelta(days=max(spans - 1, 0))


# ─── Enums ───────────────────────────────────────────────────────────────────

class Priority(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class Shift(str, Enum):
    day = "day"
    night = "night"


# ─── Graph ───────────────────────────────────────────────────────────────────

class RoadNode(BaseModel):
    node_id: int
    lon: float
    lat: float


class RoadEdge(BaseModel):
    source: int
    target: int
    weight: float  # metres


# ─── Well ────────────────────────────────────────────────────────────────────

class Well(BaseModel):
    uwi: str
    latitude: float
    longitude: float
    well_name: str
    nearest_node_id: int = 0


# ─── Vehicle ─────────────────────────────────────────────────────────────────

class Vehicle(BaseModel):
    vehicle_id: int
    name: str
    reg_plate: str = ""
    start_node: int
    current_lon: float
    current_lat: float
    pos_timestamp: int        # Unix
    free_at: datetime
    avg_speed_kmh: float
    skills: list[str]         # compatible task_types
    is_busy: bool


# ─── Task ────────────────────────────────────────────────────────────────────

class Task(BaseModel):
    task_id: str
    priority: Priority
    planned_start: datetime
    planned_duration_hours: float
    destination_uwi: str
    task_type: str
    shift: Shift
    start_day: date
    # Computed at runtime
    destination_node: int = 0
    dest_lon: float = 0.0
    dest_lat: float = 0.0
    tw_start: Optional[datetime] = None
    tw_end: Optional[datetime] = None
    service_minutes: int = 0
    penalty_weight: float = 1.0
    # §4.5 вычисляемое поле: start_day + ceil(duration / SHIFT_HOURS) - 1
    end_day: Optional[date] = None


# ─── Route ───────────────────────────────────────────────────────────────────

class RouteResult(BaseModel):
    distance_km: float
    time_minutes: float
    nodes: list[int]
    coords: list[tuple[float, float]]   # (lon, lat)


# ─── Candidate ───────────────────────────────────────────────────────────────

class VehicleCandidate(BaseModel):
    wialon_id: int
    name: str
    eta_minutes: float
    distance_km: float
    score: float
    reason: str
    route: Optional[RouteResult] = None


# ─── API Schemas ─────────────────────────────────────────────────────────────

class RecommendationRequest(BaseModel):
    task_id: str
    priority: Priority
    destination_uwi: str
    planned_start: datetime
    duration_hours: float

class RecommendationResponse(BaseModel):
    units: list[VehicleCandidate]


class RouteFromModel(BaseModel):
    wialon_id: Optional[int] = None
    lon: Optional[float] = None
    lat: Optional[float] = None

class RouteToModel(BaseModel):
    uwi: Optional[str] = None
    lon: Optional[float] = None
    lat: Optional[float] = None

class RouteRequest(BaseModel):
    from_: RouteFromModel = Field(..., alias="from")
    to: RouteToModel

    model_config = {"populate_by_name": True}

class RouteResponse(BaseModel):
    distance_km: float
    time_minutes: float
    nodes: list[int]
    coords: list[tuple[float, float]]


class MultitaskConstraints(BaseModel):
    max_total_time_minutes: int = 480
    max_detour_ratio: float = 1.3

class MultitaskRequest(BaseModel):
    task_ids: list[str]
    constraints: MultitaskConstraints = MultitaskConstraints()

class MultitaskResponse(BaseModel):
    groups: list[list[str]]
    group_vehicles: list = []
    strategy_summary: str
    total_distance_km: float
    total_time_minutes: float
    baseline_distance_km: float
    baseline_time_minutes: float
    savings_percent: float
    reason: str


# ─── VRP Optimize ─────────────────────────────────────────────────────────────

class OptimizeRequest(BaseModel):
    task_ids: list[str]
    time_limit_seconds: int = 10

class VRPRouteItem(BaseModel):
    vehicle_id: int
    vehicle_name: str
    task_ids: list[str]
    distance_km: float

class OptimizeResponse(BaseModel):
    # OR-Tools VRP result
    vrp_routes: list[VRPRouteItem]
    vrp_total_distance_km: float
    vrp_total_time_minutes: float
    vrp_dropped_tasks: list[str]
    vrp_status: str
    vrp_total_idle_minutes: float = 0.0
    vrp_vehicle_idle_minutes: dict[int, float] = Field(default_factory=dict)
    # Greedy baseline
    greedy_routes: list[VRPRouteItem]
    greedy_total_distance_km: float
    greedy_total_time_minutes: float
    greedy_dropped_tasks: list[str]
    greedy_total_idle_minutes: float = 0.0
    # Comparison
    improvement_km: float
    improvement_percent: float
    idle_saved_minutes: float = 0.0   # сколько минут простоя сэкономил VRP vs greedy
    summary: str


# ─── Simulate ─────────────────────────────────────────────────────────────────

class SimulateRequest(BaseModel):
    start: datetime
    hours: int = 24

class SimTaskResult(BaseModel):
    task_id: str
    priority: str
    vehicle_name: str
    planned_start: datetime
    actual_start: datetime
    travel_km: float
    is_late: bool
    delay_minutes: float

class SimulateResponse(BaseModel):
    baseline_distance_km: float
    optimized_distance_km: float
    savings_percent: float
    savings_km: float
    late_tasks_baseline: int
    late_tasks_optimized: int
    total_tasks: int
    unassigned_baseline: int
    unassigned_optimized: int
    horizon_start: datetime
    horizon_hours: int
    baseline_assignments: list[SimTaskResult]
    optimized_assignments: list[SimTaskResult]
    # Idle time (ТЗ §3: суммарный простой техники)
    baseline_idle_minutes: float = 0.0
    optimized_idle_minutes: float = 0.0
    idle_saved_minutes: float = 0.0
    baseline_vehicle_idle: dict[int, float] = Field(default_factory=dict)
    optimized_vehicle_idle: dict[int, float] = Field(default_factory=dict)
    summary: str

# ─── Validated coordinate models (новые, с валидацией) ────────────────────────

from pydantic import field_validator

class ValidatedRouteFromModel(BaseModel):
    wialon_id: Optional[int] = None
    lon: Optional[float] = Field(None, ge=-180.0, le=180.0)
    lat: Optional[float] = Field(None, ge=-90.0, le=90.0)

    @field_validator("lon", "lat", mode="before")
    @classmethod
    def check_not_zero_island(cls, v):
        return v


class ValidatedRouteToModel(BaseModel):
    uwi: Optional[str] = None
    lon: Optional[float] = Field(None, ge=-180.0, le=180.0)
    lat: Optional[float] = Field(None, ge=-90.0, le=90.0)


def _max_multitask() -> int:
    from app.core.config import settings
    return settings.max_multitask_tasks


def _max_vrp() -> int:
    from app.core.config import settings
    return settings.max_vrp_tasks


class ValidatedMultitaskRequest(BaseModel):
    task_ids: list[str] = Field(..., min_length=1)
    constraints: MultitaskConstraints = MultitaskConstraints()

    @classmethod
    def model_validator_task_ids(cls, v):
        limit = _max_multitask()
        if len(v) > limit:
            raise ValueError(f"Превышен лимит заявок для мультизадачи: максимум {limit}")
        return v

    from pydantic import field_validator
    @field_validator("task_ids")
    @classmethod
    def check_multitask_limit(cls, v):
        from app.core.config import settings
        limit = settings.max_multitask_tasks
        if len(v) > limit:
            raise ValueError(f"Превышен лимит: максимум {limit} заявок для мультизадачи")
        return v


class ValidatedOptimizeRequest(BaseModel):
    task_ids: list[str] = Field(..., min_length=1)
    time_limit_seconds: int = Field(10, ge=1, le=60)

    from pydantic import field_validator
    @field_validator("task_ids")
    @classmethod
    def check_vrp_limit(cls, v):
        from app.core.config import settings
        limit = settings.max_vrp_tasks
        if len(v) > limit:
            raise ValueError(f"Превышен лимит: максимум {limit} заявок для VRP")
        return v


class ValidatedSimulateRequest(BaseModel):
    start: datetime
    hours: int = Field(24, ge=1, le=168)  # max 1 week