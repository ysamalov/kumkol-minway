"""
Recommendation Service
=======================
Бизнес-логика подбора техники и группировки заявок.
Оркестрирует TaskService, Optimizer, FleetManager и AIExplainer.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from app.core.exceptions import AlgorithmTimeoutError, ValidationError
from app.core.models import Task, VehicleCandidate
from app.fleet.manager import FleetManager
from app.optimizer.optimizer import Optimizer
from app.services.task_service import TaskService

# Таймаут для оптимизатора группировки
from app.core.config import settings as _cfg_rec
GROUPING_TIMEOUT = _cfg_rec.timeout_grouping
RECOMMEND_TIMEOUT = _cfg_rec.timeout_recommend


class RecommendationService:
    def __init__(
        self,
        task_service: TaskService,
        optimizer: Optimizer,
        fleet: FleetManager,
        explainer=None,
    ) -> None:
        self.task_service = task_service
        self.optimizer = optimizer
        self.fleet = fleet
        self.explainer = explainer

    async def recommend_for_task(
        self,
        task: Task,
        top_n: int = 3,
    ) -> list[VehicleCandidate]:
        """Подбирает top_n кандидатов для одной заявки."""
        try:
            candidates = await asyncio.wait_for(
                asyncio.to_thread(self.optimizer.recommend, task, top_n),
                timeout=RECOMMEND_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise AlgorithmTimeoutError("recommend", RECOMMEND_TIMEOUT)

        if self.explainer:
            candidates = await self._enrich_with_ai(candidates, task)

        return candidates

    async def group_tasks(
        self,
        tasks: list[Task],
        max_detour_ratio: float = 1.3,
        max_total_time_min: int = 480,
    ) -> dict:
        """Группирует заявки и обогащает объяснение через AI."""
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    self.optimizer.group_tasks,
                    tasks,
                    max_detour_ratio,
                    max_total_time_min,
                ),
                timeout=GROUPING_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise AlgorithmTimeoutError("group_tasks", GROUPING_TIMEOUT)

        if self.explainer:
            result["reason"] = await self.explainer.explain_grouping(
                strategy=result["strategy_summary"],
                groups=result["groups"],
                savings_percent=result["savings_percent"],
                total_distance_km=result["total_distance_km"],
                baseline_distance_km=result["baseline_distance_km"],
                fallback_reason=result["reason"],
            )

        return result

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _enrich_with_ai(
        self, candidates: list[VehicleCandidate], task: Task
    ) -> list[VehicleCandidate]:
        """Параллельно обогащает все кандидаты AI-объяснениями."""
        async def enrich_one(c: VehicleCandidate) -> VehicleCandidate:
            vehicle = self.fleet.vehicles.get(c.wialon_id)
            wait = self.fleet.wait_minutes(vehicle) if vehicle else 0.0
            compatible = (
                self.fleet.is_compatible(vehicle, task.task_type)
                if vehicle else True
            )
            c.reason = await self.explainer.explain_recommendation(
                vehicle_name=c.name,
                distance_km=c.distance_km,
                eta_minutes=c.eta_minutes,
                wait_minutes=wait,
                is_compatible=compatible,
                priority=task.priority.value,
                task_type=task.task_type,
                score=c.score,
                fallback_reason=c.reason,
            )
            return c

        return list(await asyncio.gather(*[enrich_one(c) for c in candidates]))
