"""
Route Service
=============
Бизнес-логика построения маршрутов.
Изолирует роутеры от прямых вызовов graph/repo.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from app.core.exceptions import (
    NotFoundError, RoutingError, AlgorithmTimeoutError, ValidationError
)
from app.core.models import RouteResult
from app.db.repository import Repository
from app.graph.road_graph import RoadGraph

# Максимальное время Dijkstra (секунды)
from app.core.config import settings as _cfg_rs
ROUTE_TIMEOUT = _cfg_rs.timeout_route


class RouteService:
    def __init__(self, repo: Repository, graph: RoadGraph) -> None:
        self.repo = repo
        self.graph = graph

    async def route_between(
        self,
        from_lon: float,
        from_lat: float,
        to_lon: float,
        to_lat: float,
        speed_kmh: float = 40.0,
    ) -> RouteResult:
        """Строит маршрут между двумя точками."""
        src = self.graph.snap_to_node(from_lon, from_lat)
        dst = self.graph.snap_to_node(to_lon, to_lat)
        return await self._dijkstra(src, dst, speed_kmh)

    async def route_to_well(
        self,
        from_lon: float,
        from_lat: float,
        uwi: str,
        speed_kmh: float = 40.0,
    ) -> RouteResult:
        """Строит маршрут от точки до скважины по UWI."""
        well = await self.repo.get_well_by_uwi(uwi)
        if not well:
            raise NotFoundError("Well", uwi)

        src = self.graph.snap_to_node(from_lon, from_lat)
        dst = self.graph.snap_to_node(well["longitude"], well["latitude"])
        return await self._dijkstra(src, dst, speed_kmh)

    async def _dijkstra(self, src: int, dst: int, speed_kmh: float) -> RouteResult:
        """Запускает Dijkstra с таймаутом."""
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(self.graph.shortest_path, src, dst),
                timeout=ROUTE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise AlgorithmTimeoutError("Dijkstra", ROUTE_TIMEOUT)
        except ValueError:
            raise RoutingError(src, dst)

        result.time_minutes = round((result.distance_km / speed_kmh) * 60, 1)
        return result
