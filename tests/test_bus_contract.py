import fakeredis
import pytest

from agenticai.bus.base import EventBus
from agenticai.bus.inmemory import InMemoryBus
from agenticai.bus.redis import RedisBus


@pytest.fixture(params=["inmemory", "redis"])
def queue_bus(request: pytest.FixtureRequest) -> EventBus:
    """Provide queue backends that must satisfy the same enqueue/dequeue contract."""
    backend = request.param
    if backend == "inmemory":
        return InMemoryBus()
    if backend == "redis":
        redis_client = fakeredis.FakeRedis(decode_responses=True)
        return RedisBus(
            "redis://unused",
            client=redis_client,
            namespace="test",
            max_attempts=1,
            backoff_seconds=0.0,
            dedupe_ttl_seconds=3600,
        )
    raise ValueError(f"Unsupported backend under test: {backend}")


def test_enqueue_dequeue_round_trip(queue_bus: EventBus) -> None:
    """Queue backends should return the same payloads and ids that were enqueued."""
    accepted = queue_bus.enqueue(
        "tasks",
        "job-1",
        {"task_id": "task-1", "status": "QUEUED"},
    )
    assert accepted is True
    messages = queue_bus.dequeue("tasks", limit=10)
    assert messages == [
        {
            "job_id": "job-1",
            "payload": {"task_id": "task-1", "status": "QUEUED"},
        }
    ]


def test_enqueue_deduplicates_by_job_id(queue_bus: EventBus) -> None:
    """Queue backends should reject duplicate deterministic job ids."""
    first = queue_bus.enqueue("tasks", "job-dup", {"task_id": "1"})
    second = queue_bus.enqueue("tasks", "job-dup", {"task_id": "1"})
    assert first is True
    assert second is False
    messages = queue_bus.dequeue("tasks", limit=10)
    assert len(messages) == 1
    assert messages[0]["job_id"] == "job-dup"


def test_publish_and_drain_compatibility(queue_bus: EventBus) -> None:
    """Legacy publish/drain behavior should still work with queue internals."""
    queue_bus.publish("events", {"kind": "task.created", "task_id": "abc"})
    queue_bus.publish("events", {"kind": "task.created", "task_id": "abc"})
    drained = queue_bus.drain("events")
    assert drained == [{"kind": "task.created", "task_id": "abc"}]


def test_ping_returns_true_for_healthy_backend(queue_bus: EventBus) -> None:
    """Healthy queue backends should pass readiness checks."""
    assert queue_bus.ping() is True
