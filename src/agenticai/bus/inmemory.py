from collections import defaultdict, deque
from threading import Lock

from agenticai.bus.base import EventBus, QueuedMessage, payload_job_id


class InMemoryBus(EventBus):
    """Simple queue-backed bus for local/dev usage."""

    def __init__(self) -> None:
        """Initialize per-topic in-memory queues."""
        self._topics: dict[str, deque[QueuedMessage]] = defaultdict(deque)
        self._ids_by_queue: dict[str, set[str]] = defaultdict(set)
        self._lock = Lock()

    def enqueue(
        self,
        queue: str,
        job_id: str,
        payload: dict[str, object],
    ) -> bool:
        """Enqueue a message once by deterministic job id."""
        with self._lock:
            if job_id in self._ids_by_queue[queue]:
                return False

            self._topics[queue].append(
                {
                    "job_id": job_id,
                    "payload": payload,
                }
            )
            self._ids_by_queue[queue].add(job_id)
            return True

    def dequeue(self, queue: str, *, limit: int = 1) -> list[QueuedMessage]:
        """Dequeue up to `limit` messages from one queue."""
        if limit < 1:
            return []

        with self._lock:
            queue_items = self._topics[queue]
            messages: list[QueuedMessage] = []
            while queue_items and len(messages) < limit:
                message = queue_items.popleft()
                messages.append(message)
                self._ids_by_queue[queue].discard(message["job_id"])
            return messages

    def publish(self, topic: str, payload: dict[str, object]) -> None:
        """Enqueue a message for a topic."""
        self.enqueue(topic, payload_job_id(topic, payload), payload)

    def drain(self, topic: str) -> list[dict[str, object]]:
        """Drain all queued messages for one topic."""
        with self._lock:
            queue = self._topics[topic]
            if not queue:
                return []
            messages = [message["payload"] for message in queue]
            ids = [message["job_id"] for message in queue]
            queue.clear()
            queue_ids = self._ids_by_queue[topic]
            for job_id in ids:
                queue_ids.discard(job_id)
            return messages

    def ping(self) -> bool:
        """In-memory bus is healthy if object exists."""
        return True
