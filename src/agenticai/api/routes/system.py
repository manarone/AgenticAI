import inspect

from fastapi import APIRouter, Request

from agenticai.core.config import get_settings

router = APIRouter(tags=["system"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe used by orchestrators."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> dict[str, str]:
    """Readiness probe that checks app state and bus availability."""
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        settings = get_settings()

    bus = getattr(request.app.state, "bus", None)
    if bus is None:
        return {"status": "not_ready", "bus_backend": settings.bus_backend}

    ping = getattr(bus, "ping", None)
    if callable(ping):
        try:
            ping_result = ping()
            if inspect.isawaitable(ping_result):
                ping_result = await ping_result
            if ping_result is False:
                return {"status": "not_ready", "bus_backend": settings.bus_backend}
        except Exception:
            return {"status": "not_ready", "bus_backend": settings.bus_backend}

    return {"status": "ready", "bus_backend": settings.bus_backend}
