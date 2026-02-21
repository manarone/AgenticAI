from agenticai.bus.base import EventBus
from agenticai.bus.inmemory import InMemoryBus
from agenticai.core.config import Settings


def create_bus(settings: Settings) -> EventBus:
    if settings.bus_backend == "inmemory":
        return InMemoryBus()

    raise NotImplementedError("Redis bus is not implemented yet")
