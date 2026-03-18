"""
Road Graph (§7.4)
=================
In-memory граф дорог на основе NetworkX + scipy KDTree.

Улучшения vs оригинал:
  - LRU cache на shortest_path — повторные запросы O(1)
  - distance_matrix_cached — кэш по frozenset узлов
  - _dijkstra выделен в приватный метод для тестирования
"""
from __future__ import annotations

import math
from typing import Optional

import networkx as nx
import numpy as np
from scipy.spatial import KDTree

from app.core.models import RouteResult

# Размер dict-кэша маршрутов
from app.core.config import settings as _cfg_rg
_ROUTE_CACHE_SIZE = _cfg_rg.road_graph_cache_size


class RoadGraph:
    """
    In-memory road graph built from road_nodes + road_edges.

    Uses:
      - NetworkX undirected weighted graph for Dijkstra
      - scipy KDTree for O(log N) snap_to_node(lon, lat)
      - dict cache for repeated shortest_path queries
    """

    def __init__(self) -> None:
        self.G: nx.Graph = nx.Graph()
        self._coords: dict[int, tuple[float, float]] = {}
        self._kd_tree: Optional[KDTree] = None
        self._kd_node_ids: list[int] = []
        # Route cache: (src, dst) -> RouteResult
        self._route_cache: dict[tuple[int, int], RouteResult] = {}
        # Distance matrix cache: (frozenset srcs, frozenset tgts) -> dict
        self._dist_matrix_cache: dict[tuple, dict] = {}

    # ── Build ─────────────────────────────────────────────────────────────────

    def build(self, nodes: list[dict], edges: list[dict]) -> None:
        for n in nodes:
            nid = n["node_id"]
            lon, lat = float(n["lon"]), float(n["lat"])
            self._coords[nid] = (lon, lat)
            self.G.add_node(nid, lon=lon, lat=lat)

        for e in edges:
            self.G.add_edge(
                e["source"], e["target"],
                weight=float(e["weight"])
            )

        self._build_kdtree()
        self.invalidate_cache()

    def _build_kdtree(self) -> None:
        ids = list(self._coords.keys())
        if not ids:
            return
        # Нормализуем координаты в метрические единицы для корректного KDTree.
        # На широте ~50° (Казахстан) 1° долготы ≈ 71 км, 1° широты ≈ 111 км.
        # Без нормализации KDTree считает евклидово расстояние в градусах
        # и снапит к неправильным узлам, что приводит к огромным маршрутам.
        lats = [self._coords[i][1] for i in ids]
        avg_lat = sum(lats) / len(lats)
        self._kd_lat_scale = math.radians(1) * 6371000.0          # м на градус широты
        self._kd_lon_scale = self._kd_lat_scale * math.cos(math.radians(avg_lat))
        pts = np.array([
            [self._coords[i][0] * self._kd_lon_scale,
             self._coords[i][1] * self._kd_lat_scale]
            for i in ids
        ])
        self._kd_tree = KDTree(pts)
        self._kd_node_ids = ids

    # ── Snap ──────────────────────────────────────────────────────────────────

    def snap_to_node(self, lon: float, lat: float) -> int:
        """Return nearest graph node_id for given (lon, lat)."""
        if self._kd_tree is None:
            raise RuntimeError("Graph is empty — no road_nodes loaded from DB")
        # Применяем тот же масштаб, что и при построении KDTree
        # float() на случай если придёт decimal.Decimal из БД
        _, idx = self._kd_tree.query([float(lon) * self._kd_lon_scale, float(lat) * self._kd_lat_scale])
        return self._kd_node_ids[idx]

    # ── Shortest path (cached) ─────────────────────────────────────────────────

    def shortest_path(self, src: int, dst: int) -> RouteResult:
        """
        Dijkstra от src до dst.
        Результат кэшируется по (src, dst) — повторные вызовы O(1).
        time_minutes заполняется вызывающей стороной.
        """
        cache_key = (src, dst)
        if cache_key in self._route_cache:
            cached = self._route_cache[cache_key]
            return RouteResult(
                distance_km=cached.distance_km,
                time_minutes=0.0,
                nodes=list(cached.nodes),
                coords=list(cached.coords),
            )

        result = self._dijkstra(src, dst)
        # Ограничиваем размер кэша
        if len(self._route_cache) >= _ROUTE_CACHE_SIZE:
            # Удаляем первый (oldest) элемент
            next(iter(self._route_cache))
            del self._route_cache[next(iter(self._route_cache))]
        self._route_cache[cache_key] = result
        return RouteResult(
            distance_km=result.distance_km,
            time_minutes=0.0,
            nodes=list(result.nodes),
            coords=list(result.coords),
        )

    def _dijkstra(self, src: int, dst: int) -> RouteResult:
        """Чистый Dijkstra без кэша — для тестов и внутреннего использования."""
        try:
            length, path = nx.single_source_dijkstra(
                self.G, src, dst, weight="weight"
            )
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            raise ValueError(f"No path from node {src} to node {dst}")

        coords = [
            (self._coords[n][0], self._coords[n][1])
            for n in path
            if n in self._coords
        ]
        return RouteResult(
            distance_km=round(length / 1000.0, 3),
            time_minutes=0.0,
            nodes=path,
            coords=coords,
        )

    # ── Batch distance matrix (cached) ────────────────────────────────────────

    def distance_matrix(
        self,
        source_nodes: list[int],
        target_nodes: list[int],
    ) -> dict[tuple[int, int], float]:
        """
        Returns {(src, tgt): distance_km} для всех пар.
        Кэшируется по frozenset узлов.
        """
        cache_key = (frozenset(source_nodes), frozenset(target_nodes))
        if cache_key in self._dist_matrix_cache:
            return self._dist_matrix_cache[cache_key]

        result: dict[tuple[int, int], float] = {}
        target_set = set(target_nodes)
        for src in source_nodes:
            lengths = nx.single_source_dijkstra_path_length(
                self.G, src, weight="weight"
            )
            for tgt in target_set:
                result[(src, tgt)] = lengths.get(tgt, math.inf) / 1000.0

        self._dist_matrix_cache[cache_key] = result
        return result

    def invalidate_cache(self) -> None:
        """Сбрасывает все кэши (вызывать после перезагрузки графа)."""
        self._route_cache.clear()
        self._dist_matrix_cache.clear()

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def node_count(self) -> int:
        return self.G.number_of_nodes()

    @property
    def edge_count(self) -> int:
        return self.G.number_of_edges()

    @property
    def cache_info(self) -> dict:
        return {
            "route_cache_size": len(self._route_cache),
            "dist_matrix_cache_size": len(self._dist_matrix_cache),
        }