"""
API Routes
==========
HTTP-слой: только валидация входа, вызов сервисов, формирование ответа.
Никакой бизнес-логики и прямых обращений к БД/графу/оптимайзеру.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException

from app.core.exceptions import AppError
from app.core.models import (
    MultitaskResponse, OptimizeResponse, VRPRouteItem,
    SimulateResponse, SimTaskResult,
    RecommendationRequest, RecommendationResponse,
    RouteRequest, RouteResponse,
    Task, Priority, Shift,
    ValidatedMultitaskRequest, ValidatedOptimizeRequest, ValidatedSimulateRequest,
)
from app.core.config import settings as _cfg
from app.dependencies import (
    get_repo, get_graph, get_fleet, get_optimizer, get_explainer, get_settings
)
from app.db.repository import Repository
from app.fleet.manager import FleetManager
from app.graph.road_graph import RoadGraph
from app.optimizer.optimizer import Optimizer
from app.services.task_service import TaskService
from app.services.route_service import RouteService
from app.services.recommendation_service import RecommendationService
from app.services.optimize_service import OptimizeService
from app.optimizer.simulator import simulate

router = APIRouter()


def _svc_error(exc: AppError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


# ── Health ─────────────────────────────────────────────────────────────────────

@router.get("/health")
async def health(
    fleet: FleetManager = Depends(get_fleet),
    graph: RoadGraph    = Depends(get_graph),
    explainer           = Depends(get_explainer),
):
    from app.core.config import settings as _s
    return {
        "status":          "ok",
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "vehicles":        len(fleet.vehicles),
        "graph_nodes":     graph.node_count,
        "graph_edges":     graph.edge_count,
        "cache":           graph.cache_info,
        "ai_enabled":      explainer is not None,
        "max_mt_tasks":    _s.max_multitask_tasks,
        "max_vrp_tasks":   _s.max_vrp_tasks,
    }


# ── Recommendations ────────────────────────────────────────────────────────────

@router.post("/recommendations", response_model=RecommendationResponse)
async def recommendations(
    body:      RecommendationRequest,
    repo:      Repository   = Depends(get_repo),
    graph:     RoadGraph    = Depends(get_graph),
    fleet:     FleetManager = Depends(get_fleet),
    optimizer: Optimizer    = Depends(get_optimizer),
    explainer               = Depends(get_explainer),
    settings                = Depends(get_settings),
):
    try:
        task_svc = TaskService(repo, graph)
        rec_svc  = RecommendationService(task_svc, optimizer, fleet, explainer)

        task = await task_svc.build_task_from_request(
            task_id=body.task_id,
            priority=body.priority,
            destination_uwi=body.destination_uwi,
            planned_start=body.planned_start,
            duration_hours=body.duration_hours,
        )
        candidates = await rec_svc.recommend_for_task(
            task, top_n=settings.top_n_recommendations
        )
        return RecommendationResponse(units=candidates)
    except AppError as e:
        raise _svc_error(e)


# ── Route ──────────────────────────────────────────────────────────────────────

@router.post("/route", response_model=RouteResponse)
async def route(
    body:     RouteRequest,
    repo:     Repository   = Depends(get_repo),
    graph:    RoadGraph    = Depends(get_graph),
    fleet:    FleetManager = Depends(get_fleet),
    settings               = Depends(get_settings),
):
    try:
        from_lon, from_lat = body.from_.lon, body.from_.lat
        spd = _cfg.default_speed_kmh

        if body.from_.wialon_id and body.from_.wialon_id in fleet.vehicles:
            v = fleet.vehicles[body.from_.wialon_id]
            if not from_lon:
                from_lon, from_lat = v.current_lon, v.current_lat
            spd = v.avg_speed_kmh or spd
        if not from_lon:
            raise HTTPException(
                status_code=400,
                detail="Provide wialon_id or lon/lat for 'from'"
            )

        to_lon, to_lat = body.to.lon, body.to.lat
        if body.to.uwi and not to_lon:
            well = await repo.get_well_by_uwi(body.to.uwi)
            if not well:
                raise HTTPException(status_code=404, detail=f"Well '{body.to.uwi}' not found")
            to_lon, to_lat = well["longitude"], well["latitude"]
        if not to_lon:
            raise HTTPException(status_code=400, detail="Provide uwi or lon/lat for 'to'")

        route_svc = RouteService(repo, graph)
        result = await route_svc.route_between(from_lon, from_lat, to_lon, to_lat, spd)
        return RouteResponse(
            distance_km=result.distance_km,
            time_minutes=result.time_minutes,
            nodes=result.nodes,
            coords=result.coords,
        )
    except AppError as e:
        raise _svc_error(e)


# ── Multitask ──────────────────────────────────────────────────────────────────

@router.post("/multitask", response_model=MultitaskResponse)
async def multitask(
    body:      ValidatedMultitaskRequest,
    repo:      Repository   = Depends(get_repo),
    graph:     RoadGraph    = Depends(get_graph),
    fleet:     FleetManager = Depends(get_fleet),
    optimizer: Optimizer    = Depends(get_optimizer),
    explainer               = Depends(get_explainer),
):
    try:
        task_svc = TaskService(repo, graph)
        rec_svc  = RecommendationService(task_svc, optimizer, fleet, explainer)

        tasks = await task_svc.load_tasks_by_ids(body.task_ids)
        result = await rec_svc.group_tasks(
            tasks,
            max_detour_ratio=body.constraints.max_detour_ratio,
            max_total_time_min=body.constraints.max_total_time_minutes,
        )
        return MultitaskResponse(**result)
    except AppError as e:
        raise _svc_error(e)


# ── Optimize ───────────────────────────────────────────────────────────────────

@router.post("/optimize", response_model=OptimizeResponse)
async def optimize(
    body:      ValidatedOptimizeRequest,
    repo:      Repository   = Depends(get_repo),
    graph:     RoadGraph    = Depends(get_graph),
    fleet:     FleetManager = Depends(get_fleet),
    optimizer: Optimizer    = Depends(get_optimizer),
):
    try:
        task_svc = TaskService(repo, graph)
        opt_svc  = OptimizeService(graph, fleet)

        tasks = await task_svc.load_tasks_by_ids(body.task_ids)
        if not tasks:
            raise HTTPException(status_code=422, detail="No tasks with valid wells")

        vrp_result, greedy_result = await opt_svc.solve(tasks, body.time_limit_seconds)

        vid_to_name = {v.vehicle_id: v.name for v in fleet.vehicles.values()}

        vrp_routes = [
            VRPRouteItem(
                vehicle_id=vid,
                vehicle_name=vid_to_name.get(vid, str(vid)),
                task_ids=tids,
                distance_km=vrp_result.vehicle_distances.get(vid, 0.0),
            )
            for vid, tids in vrp_result.routes.items()
        ]
        greedy_routes = [
            VRPRouteItem(
                vehicle_id=vid,
                vehicle_name=vid_to_name.get(vid, str(vid)),
                task_ids=tids,
                distance_km=0.0,
            )
            for vid, tids in greedy_result.routes.items()
        ]

        g_dist = greedy_result.total_distance_km
        v_dist = vrp_result.total_distance_km
        improvement_km  = round(g_dist - v_dist, 2)
        improvement_pct = round((improvement_km / g_dist * 100) if g_dist > 0 else 0.0, 1)
        idle_saved = round(greedy_result.total_idle_minutes - vrp_result.total_idle_minutes, 1)

        if improvement_pct > 5:
            summary = (f"OR-Tools сократил пробег на {improvement_km} км ({improvement_pct}%), "
                       f"простой сокращён на {max(0, idle_saved)} мин.")
        elif improvement_pct > 0:
            summary = (f"Небольшое улучшение: {improvement_km} км ({improvement_pct}%).")
        else:
            summary = (f"Жадный алгоритм близок к оптимуму. VRP статус: {vrp_result.solver_status}.")

        return OptimizeResponse(
            vrp_routes=vrp_routes,
            vrp_total_distance_km=v_dist,
            vrp_total_time_minutes=vrp_result.total_time_minutes,
            vrp_dropped_tasks=vrp_result.dropped_tasks,
            vrp_status=vrp_result.solver_status,
            vrp_total_idle_minutes=vrp_result.total_idle_minutes,
            vrp_vehicle_idle_minutes=vrp_result.vehicle_idle_minutes,
            greedy_routes=greedy_routes,
            greedy_total_distance_km=g_dist,
            greedy_total_time_minutes=greedy_result.total_time_minutes,
            greedy_dropped_tasks=greedy_result.dropped_tasks,
            greedy_total_idle_minutes=greedy_result.total_idle_minutes,
            improvement_km=improvement_km,
            improvement_percent=improvement_pct,
            idle_saved_minutes=max(0.0, idle_saved),
            summary=summary,
        )
    except AppError as e:
        raise _svc_error(e)


# ── Simulate ───────────────────────────────────────────────────────────────────

@router.post("/simulate", response_model=SimulateResponse)
async def simulate_endpoint(
    body:      ValidatedSimulateRequest,
    repo:      Repository   = Depends(get_repo),
    graph:     RoadGraph    = Depends(get_graph),
    fleet:     FleetManager = Depends(get_fleet),
):
    try:
        horizon_start = body.start
        if horizon_start.tzinfo is None:
            horizon_start = horizon_start.replace(tzinfo=timezone.utc)
        horizon_end = horizon_start + timedelta(hours=body.hours)

        task_svc = TaskService(repo, graph)
        opt_svc  = OptimizeService(graph, fleet)

        tasks    = await task_svc.load_tasks_in_window(horizon_start, horizon_end)
        if not tasks:
            raise HTTPException(
                status_code=404,
                detail=f"No tasks in window {horizon_start} – {horizon_end}",
            )

        dist_matrix  = await opt_svc.build_dist_matrix(tasks)

        # Используем отфильтрованные машины (ближайшие, не занятые) — как в VRP
        from app.core.config import settings as _sim_cfg
        all_vehicles = list(fleet.vehicles.values())
        task_nodes   = list({t.destination_node for t in tasks})
        vehicles = [
            v for v in all_vehicles
            if not v.is_busy and any(
                dist_matrix.get((v.start_node, tn), dist_matrix.get((tn, v.start_node), 999999.0))
                < _sim_cfg.vrp_max_distance_km * 1.5
                for tn in task_nodes
            )
        ] or all_vehicles  # fallback если все отфильтрованы

        baseline  = simulate(tasks, vehicles, dist_matrix, horizon_start, body.hours,
                             mode="greedy",    is_compatible_fn=fleet.is_compatible)
        optimized = simulate(tasks, vehicles, dist_matrix, horizon_start, body.hours,
                             mode="optimized", is_compatible_fn=fleet.is_compatible)

        b_dist = baseline.total_distance_km
        o_dist = optimized.total_distance_km
        savings_km  = round(b_dist - o_dist, 2)
        savings_pct = round((savings_km / b_dist * 100) if b_dist > 0 else 0.0, 1)

        idle_saved = round(baseline.total_idle_minutes - optimized.total_idle_minutes, 1)

        if savings_pct > 10:
            summary = (f"Оптимизированный алгоритм сэкономил {savings_km} км ({savings_pct}%) "
                       f"и снизил просроченные заявки с {baseline.late_tasks} до {optimized.late_tasks}. "
                       f"Простой сокращён на {max(0, idle_saved)} мин.")
        elif savings_pct > 0:
            summary = (f"Умеренное улучшение: -{savings_km} км ({savings_pct}%). "
                       f"Просроченных: {baseline.late_tasks} → {optimized.late_tasks}.")
        else:
            summary = f"Жадный алгоритм близок к оптимуму. Просроченных: {baseline.late_tasks}."

        def to_results(sim_result) -> list[SimTaskResult]:
            return [SimTaskResult(
                task_id=a.task_id,
                priority=a.priority,
                vehicle_name=a.vehicle_name,
                planned_start=a.planned_start,
                actual_start=a.actual_start,
                travel_km=a.travel_km,
                is_late=a.is_late,
                delay_minutes=a.delay_minutes,
            ) for a in sim_result.assignments]

        return SimulateResponse(
            baseline_distance_km=b_dist,
            optimized_distance_km=o_dist,
            savings_percent=savings_pct,
            savings_km=savings_km,
            late_tasks_baseline=baseline.late_tasks,
            late_tasks_optimized=optimized.late_tasks,
            total_tasks=baseline.total_tasks,
            unassigned_baseline=len(baseline.unassigned),
            unassigned_optimized=len(optimized.unassigned),
            horizon_start=horizon_start,
            horizon_hours=body.hours,
            baseline_assignments=to_results(baseline),
            optimized_assignments=to_results(optimized),
            baseline_idle_minutes=baseline.total_idle_minutes,
            optimized_idle_minutes=optimized.total_idle_minutes,
            idle_saved_minutes=max(0.0, idle_saved),
            baseline_vehicle_idle=baseline.vehicle_idle_minutes,
            optimized_vehicle_idle=optimized.vehicle_idle_minutes,
            summary=summary,
        )
    except AppError as e:
        raise _svc_error(e)


# ── List endpoints (read-only) ─────────────────────────────────────────────────

@router.get("/list_tasks")
async def list_tasks(repo: Repository = Depends(get_repo)):
    rows = await repo.pool.fetch(
        "SELECT task_id, priority, task_type, destination_uwi, planned_start, planned_duration_hours "
        "FROM tasks ORDER BY planned_start, task_id"
    )
    return {"tasks": [
        {
            "task_id":                r["task_id"],
            "priority":               r["priority"],
            "task_type":              r["task_type"],
            "destination_uwi":        r["destination_uwi"],
            "planned_start":          r["planned_start"].isoformat() if r["planned_start"] else "",
            "planned_duration_hours": float(r["planned_duration_hours"]),
        }
        for r in rows
    ]}


@router.get("/list_wells")
async def list_wells(
    repo: Repository = Depends(get_repo),
    with_coords: bool = False,
):
    rows = await repo.pool.fetch(
        'SELECT uwi, well_name, latitude, longitude FROM "references".wells '
        'WHERE latitude IS NOT NULL ORDER BY uwi'
    )
    if with_coords:
        return {"wells": [
            {
                "uwi": r["uwi"],
                "well_name": r["well_name"] or r["uwi"],
                "lat": float(r["latitude"]),
                "lon": float(r["longitude"]),
            }
            for r in rows
        ]}
    return {"wells": [{"uwi": r["uwi"], "well_name": r["well_name"] or r["uwi"]} for r in rows]}


@router.get("/list_vehicles")
async def list_vehicles(fleet: FleetManager = Depends(get_fleet)):
    return {"vehicles": [
        {"vehicle_id": v.vehicle_id, "name": v.name,
         "lon": v.current_lon, "lat": v.current_lat}
        for v in fleet.vehicles.values()
    ]}


# ── Multistop route ────────────────────────────────────────────────────────────

@router.post("/multistop_route")
async def multistop_route(
    body: dict,
    repo:  Repository   = Depends(get_repo),
    graph: RoadGraph    = Depends(get_graph),
    fleet: FleetManager = Depends(get_fleet),
):
    try:
        vehicle_id = body.get("vehicle_id")
        task_ids   = body.get("task_ids", [])
        if not task_ids:
            raise HTTPException(status_code=400, detail="task_ids required")

        # Приоритет: vehicle_id из fleet → from_lon/from_lat → первая задача как fallback
        if vehicle_id and int(vehicle_id) in fleet.vehicles:
            v = fleet.vehicles[int(vehicle_id)]
            current_node = v.start_node
            current_lon  = v.current_lon
            current_lat  = v.current_lat
            speed        = v.avg_speed_kmh or 40.0
        elif body.get("from_lon") and body.get("from_lat"):
            current_lon  = float(body["from_lon"])
            current_lat  = float(body["from_lat"])
            current_node = graph.snap_to_node(current_lon, current_lat)
            speed        = _cfg.default_speed_kmh
        else:
            # Fallback: стартуем от первой задачи маршрута
            # (vehicle есть в VRP-решении, но уже не в текущем fleet snapshot)
            first_task_ids = task_ids[:1]
            first_raw = await repo.get_tasks_by_ids(first_task_ids)
            if first_raw:
                first_well = await repo.get_well_by_uwi(first_raw[0]["destination_uwi"])
                if first_well:
                    current_lon  = float(first_well["longitude"])
                    current_lat  = float(first_well["latitude"])
                    current_node = graph.snap_to_node(current_lon, current_lat)
                    speed        = _cfg.default_speed_kmh
                else:
                    raise HTTPException(status_code=400, detail="Provide vehicle_id or from_lon/from_lat")
            else:
                raise HTTPException(status_code=400, detail="Provide vehicle_id or from_lon/from_lat")

        raw_tasks = await repo.get_tasks_by_ids(task_ids)
        task_map  = {r["task_id"]: r for r in raw_tasks}

        route_svc = RouteService(repo, graph)
        full_coords: list[tuple[float, float]] = [(current_lon, current_lat)]
        stop_indices: list[int] = []
        stops: list[dict] = []
        total_distance_km = 0.0
        total_time_min    = 0.0

        for tid in task_ids:
            rt = task_map.get(tid)
            if not rt:
                continue
            well = await repo.get_well_by_uwi(rt["destination_uwi"])
            if not well:
                continue

            dest_lon  = float(well["longitude"])
            dest_lat  = float(well["latitude"])
            dest_node = graph.snap_to_node(dest_lon, dest_lat)

            try:
                seg_result = await route_svc.route_between(
                    current_lon, current_lat, dest_lon, dest_lat, speed
                )
                route_coords = list(seg_result.coords)
                route_dist   = seg_result.distance_km
            except AppError:
                route_coords = [(current_lon, current_lat), (dest_lon, dest_lat)]
                route_dist   = 0.0

            seg = route_coords[1:] if len(full_coords) > 1 or len(route_coords) > 1 else route_coords
            full_coords.extend(seg)
            stop_indices.append(len(full_coords) - 1)

            eta = (route_dist / speed) * 60.0
            total_distance_km += route_dist
            total_time_min    += eta + float(rt["planned_duration_hours"]) * 60.0

            stops.append({
                "task_id":          tid,
                "uwi":              well["uwi"],
                "well_name":        well.get("well_name") or well["uwi"],
                "lon":              dest_lon,
                "lat":              dest_lat,
                "leg_distance_km":  round(route_dist, 2),
                "leg_eta_minutes":  round(eta, 1),
                "service_minutes":  int(float(rt["planned_duration_hours"]) * 60),
                "cumulative_km":    round(total_distance_km, 2),
                "stop_order":       len(stops) + 1,
            })

            current_node = dest_node
            current_lon  = dest_lon
            current_lat  = dest_lat

        return {
            "coords":             full_coords,
            "stop_indices":       stop_indices,
            "stops":              stops,
            "total_distance_km":  round(total_distance_km, 2),
            "total_time_minutes": round(total_time_min, 1),
            "vehicle_id":         vehicle_id,
            "task_count":         len(stops),
            "vehicle_start":      list(full_coords[0]) if full_coords else None,
        }
    except AppError as e:
        raise _svc_error(e)


# ── Task coords (helper for map) ───────────────────────────────────────────────

@router.post("/task_coords")
async def task_coords_endpoint(
    body: dict,
    repo: Repository = Depends(get_repo),
):
    task_ids  = body.get("task_ids", [])
    raw_tasks = await repo.get_tasks_by_ids(task_ids)
    result = {}
    for rt in raw_tasks:
        well = await repo.get_well_by_uwi(rt["destination_uwi"])
        if well:
            result[rt["task_id"]] = {
                "lon": float(well["longitude"]),
                "lat": float(well["latitude"]),
                "uwi": well["uwi"],
            }
    return result