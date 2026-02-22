import logging
from typing import cast

from agenticai.bus.base import EventBus
from agenticai.bus.inmemory import InMemoryBus
from agenticai.bus.redis import RedisBus
from agenticai.core.config import Settings

logger = logging.getLogger(__name__)


def create_bus(
    settings: Settings,
    *,
    redis_fallback_to_inmemory: bool | None = None,
) -> EventBus:
    """Create the configured event bus backend."""
    if settings.bus_backend == "inmemory":
        return InMemoryBus()
    if settings.bus_backend == "redis":
        bus = RedisBus(cast(str, settings.redis_url))
        fallback_enabled = (
            settings.bus_redis_fallback_to_inmemory
            if redis_fallback_to_inmemory is None
            else redis_fallback_to_inmemory
        )
        if not fallback_enabled:
            return bus

        try:
            if bus.ping():
                return bus
            logger.warning(
                "Redis BUS_BACKEND health check failed at startup; falling back to in-memory bus"
            )
        except Exception:
            logger.warning(
                "Redis BUS_BACKEND initialization failed at startup; falling back to in-memory bus",
                exc_info=True,
            )
        return InMemoryBus()

    raise ValueError(f"Unsupported BUS_BACKEND: {settings.bus_backend}")
