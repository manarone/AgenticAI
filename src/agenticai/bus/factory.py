from agenticai.bus.base import EventBus
from agenticai.bus.inmemory import InMemoryBus
from agenticai.core.config import Settings


def create_bus(settings: Settings) -> EventBus:
    """Create the configured event bus backend."""
    if settings.bus_backend == "inmemory":
        return InMemoryBus()

    raise ValueError(f"Unsupported BUS_BACKEND: {settings.bus_backend}")
