"""
Domain exceptions (§ errors)
=============================
Иерархия ошибок приложения. Все кидаются из сервисного слоя,
перехватываются глобальными handlers в main.py.
"""
from __future__ import annotations


class AppError(Exception):
    """Базовый класс для всех доменных ошибок."""
    status_code: int = 500

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


# ── 404 ───────────────────────────────────────────────────────────────────────

class NotFoundError(AppError):
    status_code = 404

    def __init__(self, entity: str, identifier: str) -> None:
        super().__init__(f"{entity} '{identifier}' not found")
        self.entity = entity
        self.identifier = identifier


# ── 422 ───────────────────────────────────────────────────────────────────────

class ValidationError(AppError):
    status_code = 422

    def __init__(self, detail: str) -> None:
        super().__init__(detail)


class RoutingError(AppError):
    status_code = 422

    def __init__(self, src: int, dst: int) -> None:
        super().__init__(f"No path between node {src} and node {dst}")
        self.src = src
        self.dst = dst


class CoordinateError(AppError):
    status_code = 422

    def __init__(self, field: str, value: float) -> None:
        super().__init__(f"Invalid coordinate '{field}': {value}")


# ── 504 ───────────────────────────────────────────────────────────────────────

class AlgorithmTimeoutError(AppError):
    status_code = 504

    def __init__(self, algorithm: str, limit_seconds: float) -> None:
        super().__init__(
            f"Algorithm '{algorithm}' timed out after {limit_seconds}s"
        )
        self.algorithm = algorithm
        self.limit_seconds = limit_seconds


# ── 503 ───────────────────────────────────────────────────────────────────────

class ServiceUnavailableError(AppError):
    status_code = 503

    def __init__(self, service: str, reason: str = "") -> None:
        msg = f"Service '{service}' unavailable"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
