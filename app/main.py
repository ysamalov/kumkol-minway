import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.ai.explainer import create_explainer
from app.api.routes.recommendations import router
from app.core.config import settings
from app.core.exceptions import AppError
from app.db.repository import Repository
from app.fleet.manager import FleetManager
from app.graph.road_graph import RoadGraph
from app.optimizer.optimizer import Optimizer

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    log.info("Connecting to PostgreSQL…")
    pool = None
    for attempt in range(1, 11):
        try:
            pool = await asyncpg.create_pool(dsn=settings.dsn, min_size=2, max_size=10)
            log.info(f"✅ PostgreSQL connected (attempt {attempt})")
            break
        except Exception as e:
            log.warning(f"DB not ready (attempt {attempt}/10): {e}")
            await asyncio.sleep(3)
    if pool is None:
        raise RuntimeError("Could not connect to PostgreSQL after 10 attempts")
    repo = Repository(pool)

    log.info("Loading road graph…")
    graph = RoadGraph()
    nodes = await repo.get_road_nodes()
    edges = await repo.get_road_edges()
    graph.build(nodes, edges)
    if graph.node_count == 0:
        log.warning("⚠️  road_nodes table is empty — graph has no data.")
    else:
        log.info(f"✅ Graph loaded: {graph.node_count} nodes, {graph.edge_count} edges")

    log.info("Loading fleet state…")
    fleet = FleetManager(repo, graph)
    await fleet.load()
    if len(fleet.vehicles) == 0:
        log.warning("⚠️  No vehicles loaded — wialon_units_snapshot tables are empty.")
    else:
        log.info(f"✅ Fleet loaded: {len(fleet.vehicles)} vehicles")

    optimizer = Optimizer(graph, fleet)

    explainer = create_explainer()
    if explainer:
        await explainer.start()
        log.info("✅ AI explainer enabled (OpenRouter)")
    else:
        log.info("ℹ️  AI explainer disabled (set OPENROUTER_API_KEY to enable)")

    app.state.pool      = pool
    app.state.repo      = repo
    app.state.graph     = graph
    app.state.fleet     = fleet
    app.state.optimizer = optimizer
    app.state.explainer = explainer
    app.state.settings  = settings

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────────
    log.info("Closing DB pool…")
    await pool.close()
    if explainer:
        await explainer.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title="ИС УТО — Маршрутизация спецтехники",
        description="Intelligent Special Vehicle Routing System for oil fields",
        version="1.0.0",
        lifespan=lifespan,
    )

    # ── Global error handlers ─────────────────────────────────────────────────

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        log.warning("Domain error [%s] %s: %s", request.url.path,
                    type(exc).__name__, exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.message, "type": type(exc).__name__},
        )

    @app.exception_handler(Exception)
    async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error at %s", request.url.path, exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error", "type": type(exc).__name__},
        )

    app.include_router(router, prefix="/api")

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    async def index():
        return RedirectResponse(url="/static/map.html")

    return app


app = create_app()
