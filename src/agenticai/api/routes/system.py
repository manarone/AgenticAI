import inspect

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from agenticai.bus.exceptions import BUS_EXCEPTIONS
from agenticai.core.config import get_settings

router = APIRouter(tags=["system"])
BUS_HEALTH_EXCEPTIONS = BUS_EXCEPTIONS


def _effective_bus_backend(bus: object, configured_backend: str) -> str:
    """Best-effort runtime backend label for readiness responses."""
    active_backend = getattr(bus, "active_backend", None)
    if isinstance(active_backend, str) and active_backend:
        return active_backend

    bus_name = type(bus).__name__.lower()
    if "inmemory" in bus_name:
        return "inmemory"
    if "redis" in bus_name:
        return "redis"
    return configured_backend


def _not_ready_response(*, configured_backend: str, effective_backend: str) -> JSONResponse:
    """Return a 503 response for readiness failures."""
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "status": "not_ready",
            "configured_bus_backend": configured_backend,
            "effective_bus_backend": effective_backend,
        },
    )


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe used by orchestrators."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """Readiness probe that checks app state, bus availability, and DB connectivity."""
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        settings = get_settings()

    coordinator_required = bool(getattr(request.app.state, "coordinator_required", True))
    if coordinator_required:
        coordinator = getattr(request.app.state, "coordinator", None)
        coordinator_running = bool(getattr(coordinator, "is_running", False))
        if coordinator is None or not coordinator_running:
            return _not_ready_response(
                configured_backend=settings.bus_backend,
                effective_backend=settings.bus_backend,
            )

    bus = getattr(request.app.state, "bus", None)
    if bus is None:
        return _not_ready_response(
            configured_backend=settings.bus_backend,
            effective_backend=settings.bus_backend,
        )
    effective_backend = _effective_bus_backend(bus, settings.bus_backend)

    ping = getattr(bus, "ping", None)
    if callable(ping):
        try:
            ping_result = ping()
            if inspect.isawaitable(ping_result):
                ping_result = await ping_result
            if ping_result is False:
                return _not_ready_response(
                    configured_backend=settings.bus_backend,
                    effective_backend=effective_backend,
                )
        except BUS_HEALTH_EXCEPTIONS:
            return _not_ready_response(
                configured_backend=settings.bus_backend,
                effective_backend=effective_backend,
            )
    effective_backend = _effective_bus_backend(bus, settings.bus_backend)

    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state,
        "db_session_factory",
        None,
    )
    if session_factory is None:
        return _not_ready_response(
            configured_backend=settings.bus_backend,
            effective_backend=effective_backend,
        )
    try:
        with session_factory() as session:
            session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return _not_ready_response(
            configured_backend=settings.bus_backend,
            effective_backend=effective_backend,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "ready",
            "configured_bus_backend": settings.bus_backend,
            "effective_bus_backend": effective_backend,
        },
    )
