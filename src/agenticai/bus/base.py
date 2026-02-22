from typing import Protocol, TypedDict

TASK_QUEUE = "tasks"


class QueuedMessage(TypedDict):
    """Canonical queue message envelope."""

    job_id: str
    payload: dict[str, object]


class EventBus(Protocol):
    """Contract for event publishing and consumption."""

    def enqueue(
        self,
        queue: str,
        job_id: str,
        payload: dict[str, object],
    ) -> bool:
        """Queue one message; returns False when a deterministic job_id already exists."""

    def dequeue(self, queue: str, *, limit: int = 1) -> list[QueuedMessage]:
        """Dequeue up to `limit` queued messages."""

    def publish(self, topic: str, payload: dict[str, object]) -> None:
        """Publish a payload to a topic."""

    def drain(self, topic: str) -> list[dict[str, object]]:
        """Consume and clear all queued events for a topic."""

    def ping(self) -> bool:
        """Return whether the bus is healthy enough for readiness checks."""
