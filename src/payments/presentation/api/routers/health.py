"""Liveness and readiness probes.

They are deliberately left unauthenticated: they expose no payment data and are
polled by Docker and by the load balancer.
"""

from __future__ import annotations

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Response, status
from sqlalchemy.ext.asyncio import AsyncEngine

from payments.infrastructure.db.health import database_is_ready
from payments.presentation.api.schemas import HealthResponse, ReadinessResponse

router = APIRouter(tags=["health"], route_class=DishkaRoute)

NOT_READY = "unavailable"
DATABASE_DOWN = "down"


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health() -> HealthResponse:
    """Report that the process is alive."""
    return HealthResponse(status="ok")


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def ready(
    response: Response,
    engine: FromDishka[AsyncEngine],
) -> ReadinessResponse:
    """Report that the process can serve traffic (database reachable)."""
    if await database_is_ready(engine):
        return ReadinessResponse(status="ok", database="ok")
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status=NOT_READY, database=DATABASE_DOWN)


__all__ = ["router"]
