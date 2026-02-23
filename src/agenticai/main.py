import asyncio
import inspect
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError

from agenticai.api.middleware import (
    EndpointRateLimitMiddleware,
    RateLimitRule,
    RequestCorrelationMiddleware,
)
from agenticai.api.router import api_router
from agenticai.bus.exceptions import BUS_EXCEPTIONS
from agenticai.bus.factory import create_bus
from agenticai.coordinator import (
    CoordinatorWorker,
    NoOpPlannerExecutorAdapter,
    PlannerExecutorAdapter,
)
from agenticai.core.config import LOCAL_ENVIRONMENTS, Settings, get_settings
from agenticai.core.logging import configure_logging
from agenticai.db.runtime_settings import read_bus_redis_fallback_override
from agenticai.db.session import build_engine, build_session_factory
from agenticai.executor import DockerRuntimeConfig, DockerRuntimeExecutor

logger = logging.getLogger(__name__)
RESOURCE_CLOSE_TIMEOUT_SECONDS = 5
RESOURCE_CLOSE_EXCEPTIONS = BUS_EXCEPTIONS + (SQLAlchemyError,)


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
        except RESOURCE_CLOSE_EXCEPTIONS:
            logger.exception("Failed to close resource via '%s'", method_name)
        return


async def _build_default_coordinator_adapter(settings: Settings) -> PlannerExecutorAdapter:
    """Select coordinator adapter based on configured runtime backend."""
    backend = settings.execution_runtime_backend
    if backend == "noop":
        return NoOpPlannerExecutorAdapter()
    if backend != "docker":
        raise ValueError(f"Unsupported EXECUTION_RUNTIME_BACKEND '{backend}'")
    try:
        return await asyncio.to_thread(
            DockerRuntimeExecutor.from_config,
            config=DockerRuntimeConfig(
                image=settings.execution_docker_image,
                timeout_seconds=settings.execution_runtime_timeout_seconds,
                memory_limit=settings.execution_docker_memory_limit or None,
                nano_cpus=settings.execution_docker_nano_cpus,
            ),
        )
    except Exception:
        if not settings.execution_docker_allow_fallback:
            logger.exception("Docker runtime initialization failed; fallback is disabled")
            raise
        logger.exception(
            "Falling back to no-op execution adapter because Docker runtime initialization failed "
            "(EXECUTION_DOCKER_ALLOW_FALLBACK=true)"
        )
        return NoOpPlannerExecutorAdapter()


def create_app(
    *,
    start_coordinator: bool = True,
    coordinator_adapter: PlannerExecutorAdapter | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()
    configure_logging(settings.log_level)
    is_local_environment = settings.environment.strip().lower() in LOCAL_ENVIRONMENTS

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Initialize and clean up application resources."""
        app.state.settings = settings
        app.state.coordinator_required = start_coordinator
        app.state.db_engine = build_engine(settings.database_url.get_secret_value())
        app.state.db_session_factory = build_session_factory(app.state.db_engine)
        redis_fallback_override = read_bus_redis_fallback_override(app.state.db_session_factory)
        app.state.bus = create_bus(
            settings,
            redis_fallback_to_inmemory=redis_fallback_override,
        )
        app.state.coordinator = None
        if start_coordinator:
            effective_adapter = coordinator_adapter
            if effective_adapter is None:
                effective_adapter = await _build_default_coordinator_adapter(settings)
            coordinator = CoordinatorWorker(
                bus=app.state.bus,
                session_factory=app.state.db_session_factory,
                adapter=effective_adapter,
                poll_interval_seconds=settings.coordinator_poll_interval_seconds,
                batch_size=settings.coordinator_batch_size,
                recovery_scan_interval_seconds=settings.task_recovery_scan_interval_seconds,
                recovery_batch_size=settings.task_recovery_batch_size,
                queued_recovery_age_seconds=settings.task_recovery_queued_age_seconds,
                running_timeout_seconds=settings.task_recovery_running_timeout_seconds,
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
            except (RuntimeError, OSError, SQLAlchemyError):
                logger.exception("Failed to dispose database engine")
        app.state.db_engine = None
        app.state.db_session_factory = None

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if is_local_environment else None,
        redoc_url="/redoc" if is_local_environment else None,
        openapi_url="/openapi.json" if is_local_environment else None,
    )
    app.add_middleware(RequestCorrelationMiddleware)
    app.add_middleware(
        EndpointRateLimitMiddleware,
        enabled=settings.enable_rate_limiting,
        rules=(
            RateLimitRule(
                method="POST",
                path="/telegram/webhook",
                max_requests=settings.telegram_webhook_rate_limit_requests,
                window_seconds=settings.telegram_webhook_rate_limit_window_seconds,
                error_code="TELEGRAM_WEBHOOK_RATE_LIMITED",
                error_message="Too many Telegram webhook requests",
            ),
            RateLimitRule(
                method="POST",
                path="/v1/tasks",
                max_requests=settings.task_create_rate_limit_requests,
                window_seconds=settings.task_create_rate_limit_window_seconds,
                error_code="TASK_CREATE_RATE_LIMITED",
                error_message="Too many task creation requests",
            ),
        ),
    )
    app.include_router(api_router)

    @app.get("/", tags=["meta"])
    def root() -> dict[str, str]:
        """Return basic service metadata."""
        payload = {
            "name": settings.app_name,
            "status": "ok",
        }
        if is_local_environment:
            payload["environment"] = settings.environment
        return payload

    return app


app = create_app()
