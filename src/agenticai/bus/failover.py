import logging
from threading import Lock

from agenticai.bus.base import EventBus, QueuedMessage
from agenticai.bus.exceptions import BUS_EXCEPTIONS

logger = logging.getLogger(__name__)
FAILOVER_EXCEPTIONS = BUS_EXCEPTIONS


class RedisFailoverBus(EventBus):
    """Redis-first bus that permanently falls back to in-memory on runtime failure."""

    def __init__(self, *, primary: EventBus, fallback: EventBus) -> None:
        self._primary = primary
        self._fallback = fallback
        self._use_fallback = False
        self._fallback_lock = Lock()

    def _activate_fallback(self, *, operation: str, error: Exception | None = None) -> None:
        with self._fallback_lock:
            if self._use_fallback:
                return
            self._use_fallback = True
        if error is None:
            logger.warning(
                "Switching queue bus from redis to in-memory fallback after %s signaled unhealthy",
                operation,
            )
            return
        logger.warning(
            "Switching queue bus from redis to in-memory fallback after %s failed",
            operation,
            exc_info=True,
        )

    @property
    def active_backend(self) -> str:
        """Report current runtime backend for readiness and observability."""
        return "inmemory" if self._use_fallback else "redis"

    def enqueue(self, queue: str, job_id: str, payload: dict[str, object]) -> bool:
        if self._use_fallback:
            return self._fallback.enqueue(queue, job_id, payload)
        try:
            return self._primary.enqueue(queue, job_id, payload)
        except FAILOVER_EXCEPTIONS as error:
            self._activate_fallback(operation="enqueue", error=error)
            return self._fallback.enqueue(queue, job_id, payload)

    def dequeue(self, queue: str, *, limit: int = 1) -> list[QueuedMessage]:
        if self._use_fallback:
            return self._fallback.dequeue(queue, limit=limit)
        try:
            return self._primary.dequeue(queue, limit=limit)
        except FAILOVER_EXCEPTIONS as error:
            self._activate_fallback(operation="dequeue", error=error)
            return self._fallback.dequeue(queue, limit=limit)

    def publish(self, topic: str, payload: dict[str, object]) -> None:
        if self._use_fallback:
            self._fallback.publish(topic, payload)
            return
        try:
            self._primary.publish(topic, payload)
        except FAILOVER_EXCEPTIONS as error:
            self._activate_fallback(operation="publish", error=error)
            self._fallback.publish(topic, payload)

    def drain(self, topic: str) -> list[dict[str, object]]:
        if self._use_fallback:
            return self._fallback.drain(topic)
        try:
            return self._primary.drain(topic)
        except FAILOVER_EXCEPTIONS as error:
            self._activate_fallback(operation="drain", error=error)
            return self._fallback.drain(topic)

    def ping(self) -> bool:
        if self._use_fallback:
            return self._fallback.ping()
        try:
            healthy = self._primary.ping()
        except FAILOVER_EXCEPTIONS as error:
            self._activate_fallback(operation="ping", error=error)
            return self._fallback.ping()
        if healthy is False:
            self._activate_fallback(operation="ping")
            return self._fallback.ping()
        return True

    def close(self) -> None:
        for bus in (self._primary, self._fallback):
            close = getattr(bus, "close", None)
            if callable(close):
                try:
                    close()
                except FAILOVER_EXCEPTIONS:
                    logger.warning("Failed to close queue bus during shutdown", exc_info=True)
