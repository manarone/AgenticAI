import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from agenticai.coordinator import (
    CoordinatorWorker,
    ExecutionResult,
    PlannerExecutorAdapter,
    PlannerExecutorHandoff,
)
from agenticai.core.config import get_settings
from agenticai.db.base import Base
from agenticai.db.models import (
    BypassMode,
    Organization,
    RuntimeSetting,
    Task,
    TaskStatus,
    User,
    UserPolicyOverride,
)
from agenticai.db.session import build_engine
from agenticai.main import create_app
from tests.jwt_utils import make_task_api_jwt

TEST_ORG_ID = "00000000-0000-0000-0000-000000000011"
TEST_USER_ID = "00000000-0000-0000-0000-000000000012"
TEST_TASK_API_JWT_SECRET = "coordinator-test-task-api-jwt-secret-4"
TEST_TASK_API_JWT_AUDIENCE = "agenticai-coordinator-tests"


def _task_api_headers() -> dict[str, str]:
    token = make_task_api_jwt(
        secret=TEST_TASK_API_JWT_SECRET,
        audience=TEST_TASK_API_JWT_AUDIENCE,
        sub=TEST_USER_ID,
        org_id=TEST_ORG_ID,
    )
    return {"Authorization": f"Bearer {token}"}


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


class CountingAdapter:
    """Adapter that records execution calls while succeeding."""

    def __init__(self) -> None:
        self.completed_calls = 0

    def execute(self, handoff: PlannerExecutorHandoff) -> ExecutionResult:
        _ = handoff
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
    monkeypatch.setenv("TASK_API_JWT_SECRET", TEST_TASK_API_JWT_SECRET)
    monkeypatch.setenv("TASK_API_JWT_AUDIENCE", TEST_TASK_API_JWT_AUDIENCE)
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
        headers=_task_api_headers(),
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
        response = client.get(f"/v1/tasks/{task_id}", headers=_task_api_headers())
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


def _wait_for_approval(
    client: TestClient,
    *,
    timeout_seconds: float = 3.0,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get("/v1/approvals", headers=_task_api_headers())
        assert response.status_code == 200
        payload = response.json()
        if payload["count"] > 0:
            return payload["items"][0]
        time.sleep(0.02)
    pytest.fail("Timed out waiting for approval record")


def _set_org_bypass_policy(client: TestClient, *, allowed: bool) -> None:
    key = f"org.{TEST_ORG_ID}.allow_user_bypass"
    with Session(bind=client.app.state.db_engine) as session:
        setting = session.get(RuntimeSetting, key)
        if setting is None:
            setting = RuntimeSetting(
                key=key,
                value="true" if allowed else "false",
                description="Test bypass policy",
            )
        else:
            setting.value = "true" if allowed else "false"
        session.add(setting)
        session.commit()


def _set_user_bypass_override(client: TestClient, *, bypass_mode: BypassMode) -> None:
    with Session(bind=client.app.state.db_engine) as session:
        override = session.execute(
            select(UserPolicyOverride).where(
                UserPolicyOverride.org_id == TEST_ORG_ID,
                UserPolicyOverride.user_id == TEST_USER_ID,
            )
        ).scalar_one_or_none()
        if override is None:
            override = UserPolicyOverride(
                org_id=TEST_ORG_ID,
                user_id=TEST_USER_ID,
                bypass_mode=bypass_mode.value,
            )
        else:
            override.bypass_mode = bypass_mode.value
        session.add(override)
        session.commit()


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

        cancel_response = client.post(f"/v1/tasks/{task_id}/cancel", headers=_task_api_headers())
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


def test_coordinator_pauses_risky_task_waiting_for_approval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Risky prompts should pause at WAITING_APPROVAL before adapter execution."""
    adapter = CountingAdapter()
    with _coordinator_client(monkeypatch, tmp_path, adapter=adapter) as client:
        task_id = _create_task(client, "delete production deploy pipeline")
        payload = _wait_for_status(client, task_id, "WAITING_APPROVAL")
        assert payload["risk_tier"] == "HIGH"
        assert payload["approval_required"] is True
        assert payload["approval_decision"] == "PENDING"
        assert payload["completed_at"] is None
        assert adapter.completed_calls == 0

        approval = _wait_for_approval(client)
        assert approval["task_id"] == task_id
        assert approval["decision"] == "PENDING"
        assert approval["task_status"] == "WAITING_APPROVAL"


def test_coordinator_resumes_after_approval_and_completes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Approved risky tasks should resume once and complete successfully."""
    adapter = CountingAdapter()
    with _coordinator_client(monkeypatch, tmp_path, adapter=adapter) as client:
        task_id = _create_task(client, "delete production cache entries")
        _wait_for_status(client, task_id, "WAITING_APPROVAL")
        approval = _wait_for_approval(client)

        decision_response = client.post(
            f"/v1/approvals/{approval['approval_id']}/decision",
            headers=_task_api_headers(),
            json={"decision": "APPROVED", "reason": "Reviewed and approved"},
        )
        assert decision_response.status_code == 200
        assert decision_response.json()["decision"] == "APPROVED"

        payload = _wait_for_status(client, task_id, "SUCCEEDED")
        assert payload["approval_decision"] == "APPROVED"
        assert payload["error_message"] is None
        assert adapter.completed_calls == 1

        approvals_response = client.get("/v1/approvals", headers=_task_api_headers())
        assert approvals_response.status_code == 200
        assert approvals_response.json()["count"] == 1
        assert approvals_response.json()["items"][0]["decision"] == "APPROVED"


def test_coordinator_denied_approval_marks_task_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Denied approvals should terminate lifecycle without execution."""
    adapter = CountingAdapter()
    with _coordinator_client(monkeypatch, tmp_path, adapter=adapter) as client:
        task_id = _create_task(client, "delete production user data")
        _wait_for_status(client, task_id, "WAITING_APPROVAL")
        approval = _wait_for_approval(client)

        decision_response = client.post(
            f"/v1/approvals/{approval['approval_id']}/decision",
            headers=_task_api_headers(),
            json={"decision": "DENIED", "reason": "Unsafe request"},
        )
        assert decision_response.status_code == 200
        assert decision_response.json()["decision"] == "DENIED"

        payload = _wait_for_status(client, task_id, "FAILED")
        assert payload["approval_decision"] == "DENIED"
        assert payload["error_message"] == "Unsafe request"
        assert adapter.completed_calls == 0


def test_coordinator_bypass_all_risk_skips_approval_pause(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ALL_RISK bypass should execute risky tasks without WAITING_APPROVAL."""
    adapter = CountingAdapter()
    with _coordinator_client(monkeypatch, tmp_path, adapter=adapter) as client:
        _set_org_bypass_policy(client, allowed=True)
        _set_user_bypass_override(client, bypass_mode=BypassMode.ALL_RISK)

        task_id = _create_task(client, "delete production cache entries")
        payload = _wait_for_status(client, task_id, "SUCCEEDED")
        assert payload["risk_tier"] == "HIGH"
        assert payload["approval_required"] is False
        assert adapter.completed_calls == 1

        approvals_response = client.get("/v1/approvals", headers=_task_api_headers())
        assert approvals_response.status_code == 200
        assert approvals_response.json()["count"] == 0


def test_org_policy_disallow_overrides_user_bypass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Org policy should force approval even when user override requests bypass."""
    adapter = CountingAdapter()
    with _coordinator_client(monkeypatch, tmp_path, adapter=adapter) as client:
        _set_org_bypass_policy(client, allowed=False)
        _set_user_bypass_override(client, bypass_mode=BypassMode.ALL_RISK)

        task_id = _create_task(client, "delete production pipeline")
        payload = _wait_for_status(client, task_id, "WAITING_APPROVAL")
        assert payload["approval_required"] is True
        assert payload["approval_decision"] == "PENDING"
        assert adapter.completed_calls == 0


def test_audit_events_capture_critical_approval_transitions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Risk pause and deny decision should be visible in audit event stream."""
    with _coordinator_client(monkeypatch, tmp_path, adapter=CountingAdapter()) as client:
        task_id = _create_task(client, "delete production user data")
        _wait_for_status(client, task_id, "WAITING_APPROVAL")
        approval = _wait_for_approval(client)

        deny_response = client.post(
            f"/v1/approvals/{approval['approval_id']}/decision",
            headers=_task_api_headers(),
            json={"decision": "DENIED", "reason": "Policy violation"},
        )
        assert deny_response.status_code == 200
        _wait_for_status(client, task_id, "FAILED")

        events_response = client.get(
            f"/v1/audit-events?task_id={task_id}",
            headers=_task_api_headers(),
        )
        assert events_response.status_code == 200
        event_types = {item["event_type"] for item in events_response.json()["items"]}
        assert "task.lifecycle.waiting_approval" in event_types
        assert "approval.decision.denied" in event_types
        assert "task.lifecycle.failed" in event_types
