import asyncio
import inspect
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from agenticai.api.router import api_router
from agenticai.bus.factory import create_bus
from agenticai.core.config import get_settings
from agenticai.core.logging import configure_logging
from agenticai.db.session import build_engine, build_session_factory

logger = logging.getLogger(__name__)
RESOURCE_CLOSE_TIMEOUT_SECONDS = 5


async def _close_resource(resource: object) -> None:
    """Close a stateful resource by trying common shutdown method names."""
    for method_name in ("aclose", "close", "shutdown", "disconnect", "stop"):
        method = getattr(resource, method_name, None)
        if not callable(method):
            continue

        try:
            result = method()
            if inspect.isawaitable(result):
                await asyncio.wait_for(result, timeout=RESOURCE_CLOSE_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning(
                "Timed out closing resource via '%s' after %s seconds",
                method_name,
                RESOURCE_CLOSE_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.exception("Failed to close resource via '%s'", method_name)
        return


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Initialize and clean up application resources."""
        app.state.settings = settings
        app.state.bus = create_bus(settings)
        app.state.db_engine = build_engine(settings.database_url.get_secret_value())
        app.state.db_session_factory = build_session_factory(app.state.db_engine)
        yield
        bus = getattr(app.state, "bus", None)
        if bus is not None:
            await _close_resource(bus)
        app.state.bus = None
        engine = getattr(app.state, "db_engine", None)
        if engine is not None:
            await _close_resource(engine)
        app.state.db_engine = None
        app.state.db_session_factory = None

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.include_router(api_router)

    @app.get("/", tags=["meta"])
    def root() -> dict[str, str]:
        """Return basic service metadata."""
        return {
            "name": settings.app_name,
            "environment": settings.environment,
            "status": "ok",
        }

    return app


app = create_app()
