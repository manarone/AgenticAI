import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from agenticai.coordinator import (
    CoordinatorWorker,
    ExecutionResult,
    PlannerExecutorAdapter,
    PlannerExecutorHandoff,
)
from agenticai.core.config import get_settings
from agenticai.db.base import Base
from agenticai.db.models import Organization, Task, TaskStatus, User
from agenticai.db.session import build_engine
from agenticai.main import create_app

TEST_ORG_ID = "00000000-0000-0000-0000-000000000011"
TEST_USER_ID = "00000000-0000-0000-0000-000000000012"
TEST_TASK_API_AUTH_TOKEN = "test-task-api-token"
TASK_API_HEADERS = {
    "Authorization": f"Bearer {TEST_TASK_API_AUTH_TOKEN}",
    "X-Actor-User-Id": TEST_USER_ID,
}


class FailingAdapter:
    """Deterministic adapter that forces task failures."""

    def execute(self, handoff: PlannerExecutorHandoff) -> ExecutionResult:
        _ = handoff
        return ExecutionResult(success=False, error_message="Planner rejected prompt")


class SlowAdapter:
    """Adapter that simulates long-running execution work."""

    def __init__(self, *, delay_seconds: float) -> None:
        self._delay_seconds = delay_seconds
        self.completed_calls = 0

    def execute(self, handoff: PlannerExecutorHandoff) -> ExecutionResult:
        _ = handoff
        time.sleep(self._delay_seconds)
        self.completed_calls += 1
        return ExecutionResult(success=True)


@contextmanager
def _coordinator_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    adapter: PlannerExecutorAdapter | None = None,
    start_coordinator: bool = True,
) -> Generator[TestClient, None, None]:
    database_url = f"sqlite:///{tmp_path}/{uuid4()}.db"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setenv("TASK_API_AUTH_TOKEN", TEST_TASK_API_AUTH_TOKEN)
    monkeypatch.setenv("COORDINATOR_POLL_INTERVAL_SECONDS", "0.01")
    monkeypatch.setenv("COORDINATOR_BATCH_SIZE", "10")
    get_settings.cache_clear()

    engine = build_engine(database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Organization(
                id=TEST_ORG_ID,
                slug="test-org",
                name="Test Org",
            )
        )
        session.add(
            User(
                id=TEST_USER_ID,
                org_id=TEST_ORG_ID,
                telegram_user_id=123456789,
                display_name="Coordinator Tester",
            )
        )
        session.commit()
    engine.dispose()

    try:
        with TestClient(
            create_app(start_coordinator=start_coordinator, coordinator_adapter=adapter)
        ) as client:
            yield client
    finally:
        get_settings.cache_clear()


def _create_task(client: TestClient, prompt: str) -> str:
    response = client.post(
        "/v1/tasks",
        headers=TASK_API_HEADERS,
        json={
            "org_id": TEST_ORG_ID,
            "requested_by_user_id": TEST_USER_ID,
            "prompt": prompt,
        },
    )
    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "QUEUED"
    return payload["task_id"]


def _wait_for_status(
    client: TestClient,
    task_id: str,
    expected_status: str,
    *,
    timeout_seconds: float = 3.0,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    last_payload: dict[str, object] | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/v1/tasks/{task_id}", headers=TASK_API_HEADERS)
        assert response.status_code == 200
        payload = response.json()
        last_payload = payload
        if payload["status"] == expected_status:
            return payload
        time.sleep(0.02)
    pytest.fail(
        f"Timed out waiting for task {task_id} to reach {expected_status}; "
        f"last status={last_payload['status'] if last_payload else 'unknown'}"
    )


def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout_seconds: float = 2.0,
    poll_seconds: float = 0.02,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(poll_seconds)
    pytest.fail("Timed out waiting for expected condition")


def test_coordinator_transitions_task_to_succeeded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Coordinator should persist QUEUED -> RUNNING -> SUCCEEDED."""
    with _coordinator_client(monkeypatch, tmp_path) as client:
        task_id = _create_task(client, "compile release notes")
        payload = _wait_for_status(client, task_id, "SUCCEEDED")
        assert payload["started_at"] is not None
        assert payload["completed_at"] is not None
        assert payload["error_message"] is None


def test_coordinator_transitions_task_to_failed_with_adapter_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Adapter failures should persist RUNNING -> FAILED with an error message."""
    with _coordinator_client(monkeypatch, tmp_path, adapter=FailingAdapter()) as client:
        task_id = _create_task(client, "do something unsupported")
        payload = _wait_for_status(client, task_id, "FAILED")
        assert payload["started_at"] is not None
        assert payload["completed_at"] is not None
        assert payload["error_message"] == "Planner rejected prompt"


def test_coordinator_requeues_message_when_mark_running_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Transient transition errors should requeue work instead of dropping it."""
    with _coordinator_client(monkeypatch, tmp_path) as client:
        coordinator = client.app.state.coordinator
        assert coordinator is not None

        mark_running_calls = {"count": 0}
        original_mark_task_running = coordinator._mark_task_running

        def flaky_mark_task_running(task_id: str) -> PlannerExecutorHandoff | None:
            mark_running_calls["count"] += 1
            if mark_running_calls["count"] == 1:
                raise RuntimeError("transient transition failure")
            return original_mark_task_running(task_id)

        monkeypatch.setattr(coordinator, "_mark_task_running", flaky_mark_task_running)

        task_id = _create_task(client, "recover from transition failure")
        payload = _wait_for_status(client, task_id, "SUCCEEDED")
        assert payload["error_message"] is None
        assert mark_running_calls["count"] >= 2


def test_coordinator_preserves_canceled_tasks_during_execution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cancellation should remain terminal even if execution finishes later."""
    slow_adapter = SlowAdapter(delay_seconds=0.3)
    with _coordinator_client(
        monkeypatch,
        tmp_path,
        adapter=slow_adapter,
    ) as client:
        task_id = _create_task(client, "slow task for cancel")
        _wait_for_status(client, task_id, "RUNNING")

        cancel_response = client.post(f"/v1/tasks/{task_id}/cancel", headers=TASK_API_HEADERS)
        assert cancel_response.status_code == 200
        assert cancel_response.json()["status"] == "CANCELED"

        payload = _wait_for_status(client, task_id, "CANCELED", timeout_seconds=1.0)
        assert payload["completed_at"] is not None

        _wait_until(lambda: slow_adapter.completed_calls >= 1, timeout_seconds=1.5)
        final_payload = _wait_for_status(client, task_id, "CANCELED", timeout_seconds=0.5)
        assert final_payload["status"] == "CANCELED"


def test_coordinator_does_not_block_http_responsiveness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Readiness checks should remain responsive while execution is running."""
    with _coordinator_client(
        monkeypatch,
        tmp_path,
        adapter=SlowAdapter(delay_seconds=0.4),
    ) as client:
        task_id = _create_task(client, "slow task for responsiveness")
        _wait_for_status(client, task_id, "RUNNING")

        start = time.perf_counter()
        ready_response = client.get("/readyz")
        elapsed = time.perf_counter() - start
        assert ready_response.status_code == 200
        assert elapsed < 0.75

        _wait_for_status(client, task_id, "SUCCEEDED")


def test_coordinator_recovery_reenqueues_stale_queued_task(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Recovery pass should re-enqueue stale QUEUED tasks that missed queue publish."""
    with _coordinator_client(monkeypatch, tmp_path, start_coordinator=False) as client:
        worker = CoordinatorWorker(
            bus=client.app.state.bus,
            session_factory=client.app.state.db_session_factory,
            queued_recovery_age_seconds=1.0,
            running_timeout_seconds=3600.0,
            recovery_scan_interval_seconds=1.0,
        )
        stale_time = datetime.now(UTC) - timedelta(seconds=120)
        with Session(bind=client.app.state.db_engine) as session:
            task = Task(
                org_id=TEST_ORG_ID,
                requested_by_user_id=TEST_USER_ID,
                status=TaskStatus.QUEUED.value,
                prompt="stale queued task",
                created_at=stale_time,
                updated_at=stale_time,
            )
            session.add(task)
            session.commit()
            task_id = task.id

        worker._recover_stale_queued_tasks()

        queued_messages = client.app.state.bus.dequeue("tasks", limit=10)
        assert len(queued_messages) == 1
        assert queued_messages[0]["job_id"] == task_id
        assert queued_messages[0]["payload"]["task_id"] == task_id


def test_coordinator_recovery_times_out_stale_running_task(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Recovery pass should transition stale RUNNING tasks to TIMED_OUT."""
    with _coordinator_client(monkeypatch, tmp_path, start_coordinator=False) as client:
        worker = CoordinatorWorker(
            bus=client.app.state.bus,
            session_factory=client.app.state.db_session_factory,
            queued_recovery_age_seconds=3600.0,
            running_timeout_seconds=1.0,
            recovery_scan_interval_seconds=1.0,
        )
        stale_time = datetime.now(UTC) - timedelta(seconds=120)
        with Session(bind=client.app.state.db_engine) as session:
            task = Task(
                org_id=TEST_ORG_ID,
                requested_by_user_id=TEST_USER_ID,
                status=TaskStatus.RUNNING.value,
                prompt="stale running task",
                created_at=stale_time,
                updated_at=stale_time,
                started_at=stale_time,
            )
            session.add(task)
            session.commit()
            task_id = task.id

        worker._recover_stale_running_tasks()

        with Session(bind=client.app.state.db_engine) as session:
            recovered = session.get(Task, task_id)
            assert recovered is not None
            assert recovered.status == TaskStatus.TIMED_OUT.value
            assert recovered.completed_at is not None
            assert recovered.error_message == "Coordinator recovery timed out a stale RUNNING task"
