from typing import Protocol


class EventBus(Protocol):
    """Contract for event publishing and consumption."""

    def publish(self, topic: str, payload: dict[str, object]) -> None:
        """Publish a payload to a topic."""

    def drain(self, topic: str) -> list[dict[str, object]]:
        """Consume and clear all queued events for a topic."""

    def ping(self) -> bool:
        """Return whether the bus is healthy enough for readiness checks."""
