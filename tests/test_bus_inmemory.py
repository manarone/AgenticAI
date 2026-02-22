from agenticai.bus.inmemory import InMemoryBus


def test_inmemory_allows_reenqueue_after_dequeue() -> None:
    """Dequeued job IDs should be reusable for recovery flows."""
    bus = InMemoryBus()

    assert bus.enqueue("tasks", "job-1", {"task_id": "task-1"}) is True
    assert bus.enqueue("tasks", "job-1", {"task_id": "task-1"}) is False

    dequeued = bus.dequeue("tasks", limit=1)
    assert len(dequeued) == 1
    assert dequeued[0]["job_id"] == "job-1"

    assert bus.enqueue("tasks", "job-1", {"task_id": "task-1"}) is True


def test_inmemory_allows_reenqueue_after_drain() -> None:
    """Drained event IDs should be reusable for compatibility retries."""
    bus = InMemoryBus()

    bus.publish("events", {"kind": "task.created", "task_id": "task-1"})
    bus.publish("events", {"kind": "task.created", "task_id": "task-1"})
    assert bus.drain("events") == [{"kind": "task.created", "task_id": "task-1"}]

    bus.publish("events", {"kind": "task.created", "task_id": "task-1"})
    assert bus.drain("events") == [{"kind": "task.created", "task_id": "task-1"}]
