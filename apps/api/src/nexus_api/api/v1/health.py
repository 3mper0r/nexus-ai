"""Liveness and readiness probes.

* Liveness answers "is the process able to serve HTTP?" It never touches
  dependencies, so a database outage does not cause an orchestrator to restart
  healthy API processes.
* Readiness answers "can this instance do useful work right now?" It checks the
  database with a bounded timeout and returns 503 when it is unavailable.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from nexus_api.api.deps import EngineDep, SettingsDep
from nexus_api.db.health import FailureReason, check_database

router = APIRouter(prefix="/health", tags=["health"])


class LivenessResponse(BaseModel):
    status: Literal["ok"]


class DependencyCheck(BaseModel):
    status: Literal["up", "down"]
    latency_ms: float | None = None
    error: FailureReason | None = None


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, DependencyCheck]


@router.get("/live", summary="Liveness probe")
async def liveness() -> LivenessResponse:
    return LivenessResponse(status="ok")


@router.get(
    "/ready",
    summary="Readiness probe",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def readiness(
    response: Response, engine: EngineDep, settings: SettingsDep
) -> ReadinessResponse:
    db = await check_database(engine, settings.db_health_timeout_seconds)
    database_check = DependencyCheck(
        status="up" if db.is_up else "down",
        latency_ms=db.latency_ms,
        error=db.failure,
    )
    if not db.is_up:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ready" if db.is_up else "not_ready",
        checks={"database": database_check},
    )
