from collections import defaultdict, deque

from agenticai.bus.base import EventBus


class InMemoryBus(EventBus):
    """Simple queue-backed bus for local/dev usage."""

    def __init__(self) -> None:
        """Initialize per-topic in-memory queues."""
        self._topics: dict[str, deque[dict[str, object]]] = defaultdict(deque)

    def publish(self, topic: str, payload: dict[str, object]) -> None:
        """Enqueue a message for a topic."""
        self._topics[topic].append(payload)

    def drain(self, topic: str) -> list[dict[str, object]]:
        """Drain all queued messages for one topic."""
        queue = self._topics[topic]
        messages = list(queue)
        queue.clear()
        return messages

    def ping(self) -> bool:
        """In-memory bus is healthy if object exists."""
        return True
