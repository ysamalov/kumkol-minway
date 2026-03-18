"""
Scoring formula (§7.5)
======================
Assigns a score ∈ [0, 1] to a (vehicle, task) candidate pair.
Higher score = better candidate.

    score = 1 / (1 + cost)

    cost = W_DIST  * dist_penalty     # normalised distance
         + W_WAIT  * wait_penalty     # время ожидания занятой машины (до free_at)
         + W_IDLE  * idle_penalty     # простой: машина свободна, но задача ещё не пришла
         + W_PRIO  * prio_penalty     # risk of SLA breach, scaled by priority
         + W_COMPAT * compat_penalty  # penalty for incompatible vehicle type

Все веса и SLA-дедлайны читаются из Settings (.env).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.models import Priority, Task, Vehicle
from app.core.config import settings


def _sla_deadline_h(priority: Priority) -> float:
    return {
        Priority.high:   settings.sla_high_hours,
        Priority.medium: settings.sla_medium_hours,
        Priority.low:    settings.sla_low_hours,
    }[priority]


def _prio_multiplier(priority: Priority) -> float:
    """Proportional to SLA weights 55/35/10 — high=1.0 baseline."""
    sla_h = settings.sla_high_hours
    sla_m = settings.sla_medium_hours
    sla_l = settings.sla_low_hours
    # Чем короче дедлайн — тем выше штраф (обратная пропорция)
    ref = sla_h  # reference = самый строгий
    return {
        Priority.high:   1.000,
        Priority.medium: round(ref / sla_m, 3),
        Priority.low:    round(ref / sla_l, 3),
    }[priority]


@dataclass
class ScoreResult:
    score: float
    reason: str


def compute_score(
    vehicle: Vehicle,
    task: Task,
    distance_km: float,
    eta_minutes: float,
    wait_minutes: float,
    is_compatible: bool,
    idle_minutes: float = 0.0,
) -> ScoreResult:
    deadline_min = _sla_deadline_h(task.priority) * 60.0

    # 1. Distance penalty [0, 1]
    dist_penalty = min(distance_km / settings.score_dist_reference_km, 1.0)

    # 2. Wait penalty — машина занята, клиент ждёт [0, 1]
    wait_penalty = min(wait_minutes / (deadline_min + 1e-9), 1.0)

    # 3. Idle penalty — машина свободна, но задача ещё не пришла [0, 1]
    idle_penalty = min(idle_minutes / (deadline_min + 1e-9), 1.0)

    # 4. Priority / SLA breach penalty
    total_arrival = eta_minutes + wait_minutes
    prio_mult = _prio_multiplier(task.priority)
    if total_arrival > deadline_min:
        breach = (total_arrival - deadline_min) / (deadline_min + 1e-9)
        prio_penalty = prio_mult * min(breach, 1.0)
    else:
        prio_penalty = 0.0

    # 5. Compatibility penalty
    compat_penalty = 0.0 if is_compatible else 1.0

    cost = (
        settings.score_w_dist   * dist_penalty  +
        settings.score_w_wait   * wait_penalty  +
        settings.score_w_idle   * idle_penalty  +
        settings.score_w_prio   * prio_penalty  +
        settings.score_w_compat * compat_penalty
    )
    score = round(1.0 / (1.0 + cost), 2)

    reason = _build_reason(
        vehicle, task, distance_km, eta_minutes, wait_minutes,
        total_arrival, deadline_min, is_compatible, idle_minutes,
    )
    return ScoreResult(score=score, reason=reason)


def _build_reason(
    vehicle: Vehicle,
    task: Task,
    distance_km: float,
    eta_minutes: float,
    wait_minutes: float,
    total_arrival: float,
    deadline_min: float,
    is_compatible: bool,
    idle_minutes: float = 0.0,
) -> str:
    parts: list[str] = []

    if not is_compatible:
        parts.append("⚠️ несовместимый тип техники")
    else:
        parts.append("совместима по типу работ")

    if wait_minutes > 0:
        parts.append(f"занята, освободится через {wait_minutes:.0f} мин")
    else:
        parts.append("свободна")

    if idle_minutes > 30:
        parts.append(f"простой до задачи {idle_minutes:.0f} мин")

    parts.append(f"расстояние {distance_km:.1f} км, ETA {eta_minutes:.0f} мин")

    if total_arrival > deadline_min:
        parts.append(
            f"⚠️ нарушение SLA: прибытие через {total_arrival:.0f} мин "
            f"при дедлайне {deadline_min:.0f} мин"
        )

    return "; ".join(parts)