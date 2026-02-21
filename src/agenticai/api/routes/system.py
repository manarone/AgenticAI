from fastapi import APIRouter, Request

from agenticai.core.config import get_settings

router = APIRouter(tags=["system"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
def readyz(request: Request) -> dict[str, str]:
    settings = getattr(request.app.state, "settings", get_settings())
    return {"status": "ready", "bus_backend": settings.bus_backend}
