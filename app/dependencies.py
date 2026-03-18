"""
Dependency Injection providers
================================
FastAPI Depends() провайдеры для всех зависимостей.
Роутеры используют только эти функции — никакого request.app.state напрямую.
"""
from __future__ import annotations

from fastapi import Request

from app.db.repository import Repository
from app.fleet.manager import FleetManager
from app.graph.road_graph import RoadGraph
from app.optimizer.optimizer import Optimizer
from app.core.config import Settings


def get_repo(request: Request) -> Repository:
    return request.app.state.repo


def get_graph(request: Request) -> RoadGraph:
    return request.app.state.graph


def get_fleet(request: Request) -> FleetManager:
    return request.app.state.fleet


def get_optimizer(request: Request) -> Optimizer:
    return request.app.state.optimizer


def get_explainer(request: Request):
    return request.app.state.explainer


def get_settings(request: Request) -> Settings:
    return request.app.state.settings
