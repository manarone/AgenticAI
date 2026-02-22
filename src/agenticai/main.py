import asyncio
import inspect
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from agenticai.api.router import api_router
from agenticai.bus.factory import create_bus
from agenticai.coordinator import CoordinatorWorker, PlannerExecutorAdapter
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


def create_app(
    *,
    start_coordinator: bool = True,
    coordinator_adapter: PlannerExecutorAdapter | None = None,
) -> FastAPI:
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
        app.state.coordinator = None
        if start_coordinator:
            coordinator = CoordinatorWorker(
                bus=app.state.bus,
                session_factory=app.state.db_session_factory,
                adapter=coordinator_adapter,
                poll_interval_seconds=settings.coordinator_poll_interval_seconds,
                batch_size=settings.coordinator_batch_size,
            )
            await coordinator.start()
            app.state.coordinator = coordinator
        yield
        coordinator = app.state.coordinator
        if coordinator is not None:
            await coordinator.stop()
        app.state.coordinator = None
        bus = getattr(app.state, "bus", None)
        if bus is not None:
            await _close_resource(bus)
        app.state.bus = None
        engine = getattr(app.state, "db_engine", None)
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                logger.exception("Failed to dispose database engine")
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
