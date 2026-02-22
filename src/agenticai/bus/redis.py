import json
import logging
import time
from collections.abc import Callable
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from agenticai.bus.base import EventBus, QueuedMessage, payload_job_id

logger = logging.getLogger(__name__)

REDIS_BACKEND_EXCEPTIONS: tuple[type[Exception], ...] = (
    RedisError,
    RuntimeError,
    TimeoutError,
    ConnectionError,
    OSError,
)


class RedisBus(EventBus):
    """Redis-backed queue bus with deterministic job ids and retry/backoff."""

    def __init__(
        self,
        redis_url: str,
        *,
        client: Redis | None = None,
        namespace: str = "agenticai",
        max_attempts: int = 3,
        backoff_seconds: float = 0.1,
        dedupe_ttl_seconds: int = 86400,
    ) -> None:
        self._client = client or Redis.from_url(redis_url, decode_responses=True)
        self._namespace = namespace
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._dedupe_ttl_seconds = dedupe_ttl_seconds

    def _queue_key(self, queue: str) -> str:
        return f"{self._namespace}:queue:{queue}"

    def _job_key(self, queue: str, job_id: str) -> str:
        return f"{self._namespace}:queue:{queue}:job:{job_id}"

    def _execute_with_retry(self, operation: Callable[[], Any]) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                return operation()
            except RedisError as exc:
                last_error = exc
                if attempt == self._max_attempts:
                    break
                delay = self._backoff_seconds * (2 ** (attempt - 1))
                time.sleep(delay)
        if last_error is not None:
            raise last_error
        raise RuntimeError("Queue operation failed without an exception")

    def enqueue(
        self,
        queue: str,
        job_id: str,
        payload: dict[str, object],
    ) -> bool:
        """Enqueue one message and prevent duplicates by deterministic job id."""
        message = json.dumps(
            {
                "job_id": job_id,
                "payload": payload,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        job_key = self._job_key(queue, job_id)
        queue_key = self._queue_key(queue)

        was_added = self._execute_with_retry(
            lambda: self._client.set(
                name=job_key,
                value=message,
                ex=self._dedupe_ttl_seconds,
                nx=True,
            )
        )
        if not was_added:
            return False

        try:
            self._execute_with_retry(lambda: self._client.rpush(queue_key, job_id))
        except REDIS_BACKEND_EXCEPTIONS as enqueue_error:
            # Roll back the dedupe marker if enqueue never made it to the queue.
            try:
                self._execute_with_retry(lambda: self._client.delete(job_key))
            except REDIS_BACKEND_EXCEPTIONS:
                logger.warning(
                    "Failed to clean up Redis dedupe marker for queue=%s job_id=%s",
                    queue,
                    job_id,
                    exc_info=True,
                )
            raise enqueue_error
        return True

    def dequeue(self, queue: str, *, limit: int = 1) -> list[QueuedMessage]:
        """Dequeue up to `limit` messages from Redis."""
        if limit < 1:
            return []

        queue_key = self._queue_key(queue)
        messages: list[QueuedMessage] = []
        while len(messages) < limit:
            job_id = self._execute_with_retry(
                lambda queue_key=queue_key: self._client.lpop(queue_key)
            )
            if job_id is None:
                break

            job_key = self._job_key(queue, job_id)
            raw_message = self._execute_with_retry(
                lambda job_key=job_key: self._client.get(job_key)
            )
            if raw_message is None:
                self._execute_with_retry(lambda job_key=job_key: self._client.delete(job_key))
                continue

            try:
                parsed = json.loads(raw_message)
            except json.JSONDecodeError:
                self._execute_with_retry(lambda job_key=job_key: self._client.delete(job_key))
                continue
            payload = parsed.get("payload")
            if not isinstance(payload, dict):
                self._execute_with_retry(lambda job_key=job_key: self._client.delete(job_key))
                continue
            self._execute_with_retry(lambda job_key=job_key: self._client.delete(job_key))
            messages.append(
                {
                    "job_id": job_id,
                    "payload": payload,
                }
            )
        return messages

    def publish(self, topic: str, payload: dict[str, object]) -> None:
        """Publish event payload through queue semantics."""
        self.enqueue(topic, payload_job_id(topic, payload), payload)

    def drain(self, topic: str) -> list[dict[str, object]]:
        """Drain all available queued events for one topic."""
        drained: list[dict[str, object]] = []
        while True:
            batch = self.dequeue(topic, limit=100)
            if not batch:
                break
            drained.extend(message["payload"] for message in batch)
        return drained

    def ping(self) -> bool:
        """Return True when Redis responds to ping within retry budget."""
        try:
            result = self._execute_with_retry(lambda: self._client.ping())
            return bool(result)
        except RedisError:
            return False
