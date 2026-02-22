import inspect

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from agenticai.core.config import get_settings

router = APIRouter(tags=["system"])


def _not_ready_response(bus_backend: str) -> JSONResponse:
    """Return a 503 response for readiness failures."""
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "not_ready", "bus_backend": bus_backend},
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

    bus = getattr(request.app.state, "bus", None)
    if bus is None:
        return _not_ready_response(settings.bus_backend)

    ping = getattr(bus, "ping", None)
    if callable(ping):
        try:
            ping_result = ping()
            if inspect.isawaitable(ping_result):
                ping_result = await ping_result
            if ping_result is False:
                return _not_ready_response(settings.bus_backend)
        except Exception:
            return _not_ready_response(settings.bus_backend)

    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state,
        "db_session_factory",
        None,
    )
    if session_factory is None:
        return _not_ready_response(settings.bus_backend)
    try:
        with session_factory() as session:
            session.execute(text("SELECT 1"))
    except Exception:
        return _not_ready_response(settings.bus_backend)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": "ready", "bus_backend": settings.bus_backend},
    )
