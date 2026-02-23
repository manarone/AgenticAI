import hmac
from uuid import uuid4

from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from agenticai.db.models import Organization, User


def _create_secondary_identity(client) -> dict[str, str]:
    """Insert and return a second org/user identity for tenant-isolation tests."""
    second_org_id = str(uuid4())
    second_user_id = str(uuid4())
    with Session(bind=client.app.state.db_engine) as session:
        session.add(
            Organization(
                id=second_org_id,
                slug=f"org-{second_org_id[:8]}",
                name="Second Org",
            )
        )
        session.add(
            User(
                id=second_user_id,
                org_id=second_org_id,
                telegram_user_id=888000111,
                display_name="Second User",
            )
        )
        session.commit()
    return {
        "org_id": second_org_id,
        "requested_by_user_id": second_user_id,
    }


def test_root(client) -> None:
    """Root route returns basic service metadata."""
    response = client.get("/")
    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "AgenticAI"
    assert payload["status"] == "ok"


def test_healthz(client) -> None:
    """Health endpoint should always report liveness."""
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz(client) -> None:
    """Readiness returns healthy status when bus and DB are initialized."""
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "configured_bus_backend": "inmemory",
        "effective_bus_backend": "inmemory",
    }


def test_readyz_not_ready_without_bus(client) -> None:
    """Readiness returns 503 when the bus is unavailable."""
    delattr(client.app.state, "bus")

    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "configured_bus_backend": "inmemory",
        "effective_bus_backend": "inmemory",
    }


def test_readyz_not_ready_when_coordinator_required_but_not_running(client) -> None:
    """Readiness should fail when task processing loop is required but unavailable."""
    client.app.state.coordinator_required = True
    client.app.state.coordinator = None

    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "configured_bus_backend": "inmemory",
        "effective_bus_backend": "inmemory",
    }


def test_readyz_not_ready_when_bus_ping_fails(client) -> None:
    """Readiness returns 503 when the queue backend reports unhealthy."""

    class UnhealthyBus:
        def ping(self) -> bool:
            return False

    client.app.state.bus = UnhealthyBus()
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "configured_bus_backend": "inmemory",
        "effective_bus_backend": "inmemory",
    }


def test_readyz_reports_effective_backend_when_runtime_differs_from_configured(client) -> None:
    """Readiness should report effective backend when failover changes runtime behavior."""

    class FallbackBus:
        active_backend = "inmemory"

        def ping(self) -> bool:
            return True

    client.app.state.settings.bus_backend = "redis"
    client.app.state.bus = FallbackBus()

    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "configured_bus_backend": "redis",
        "effective_bus_backend": "inmemory",
    }


def test_readyz_not_ready_without_db_session_factory(client) -> None:
    """Readiness returns 503 when database session factory is unavailable."""
    client.app.state.db_session_factory = None

    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "configured_bus_backend": "inmemory",
        "effective_bus_backend": "inmemory",
    }


def test_readyz_not_ready_when_db_check_fails(client) -> None:
    """Readiness returns 503 when the DB liveness query fails."""

    class BrokenSession:
        def __enter__(self) -> "BrokenSession":
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

        def execute(self, *_args: object, **_kwargs: object) -> None:
            raise SQLAlchemyError("database unavailable")

    client.app.state.db_session_factory = lambda: BrokenSession()
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "configured_bus_backend": "inmemory",
        "effective_bus_backend": "inmemory",
    }


def test_task_routes_require_authentication(client) -> None:
    """Task APIs reject unauthenticated callers."""
    response = client.get("/v1/tasks")
    assert response.status_code == 401


def test_task_routes_reject_non_uuid_actor_header(client, task_api_headers) -> None:
    """Task APIs should reject malformed actor identifiers before DB lookup."""
    response = client.get(
        "/v1/tasks",
        headers={**task_api_headers, "X-Actor-User-Id": "not-a-uuid"},
    )
    assert response.status_code == 401
    assert response.json() == {
        "detail": {
            "code": "TASK_API_UNAUTHORIZED",
            "message": "X-Actor-User-Id must be a valid UUID",
        }
    }


def test_task_routes_require_actor_signature_when_configured(client, task_api_headers) -> None:
    """When actor signing secret is configured, requests must include a valid signature."""
    actor_id = task_api_headers["X-Actor-User-Id"]
    client.app.state.settings.task_api_actor_hmac_secret = SecretStr("actor-signing-secret")

    missing_signature = client.get("/v1/tasks", headers=task_api_headers)
    assert missing_signature.status_code == 401
    assert missing_signature.json() == {
        "detail": {
            "code": "TASK_API_UNAUTHORIZED",
            "message": "Invalid or missing X-Actor-Signature header",
        }
    }

    invalid_signature = client.get(
        "/v1/tasks",
        headers={**task_api_headers, "X-Actor-Signature": "sha256=bad-signature"},
    )
    assert invalid_signature.status_code == 401

    signature = hmac.new(
        b"actor-signing-secret",
        actor_id.encode("utf-8"),
        "sha256",
    ).hexdigest()
    valid_response = client.get(
        "/v1/tasks",
        headers={**task_api_headers, "X-Actor-Signature": f"sha256={signature}"},
    )
    assert valid_response.status_code == 200

    # Uppercase UUID should still work because server canonicalizes UUID casing.
    uppercase_actor_response = client.get(
        "/v1/tasks",
        headers={
            **task_api_headers,
            "X-Actor-User-Id": actor_id.upper(),
            "X-Actor-Signature": f"sha256={signature}",
        },
    )
    assert uppercase_actor_response.status_code == 200


def test_list_tasks(client, task_api_headers) -> None:
    """Task listing starts empty in a fresh test database."""
    response = client.get("/v1/tasks", headers=task_api_headers)
    assert response.status_code == 200
    assert response.json() == {"items": [], "count": 0}


def test_create_task(client, seeded_identity, task_api_headers) -> None:
    """Task creation persists and returns lifecycle fields."""
    response = client.post(
        "/v1/tasks",
        headers=task_api_headers,
        json={
            **seeded_identity,
            "prompt": "build a release checklist",
        },
    )
    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "QUEUED"
    assert payload["task_id"]
    assert payload["org_id"] == seeded_identity["org_id"]
    assert payload["requested_by_user_id"] == seeded_identity["requested_by_user_id"]
    assert payload["created_at"]
    assert payload["updated_at"]
    assert payload["completed_at"] is None
    queued_messages = client.app.state.bus.dequeue("tasks", limit=10)
    assert len(queued_messages) == 1
    assert queued_messages[0]["job_id"] == payload["task_id"]
    assert queued_messages[0]["payload"]["task_id"] == payload["task_id"]


def test_create_task_ignores_spoofed_payload_identity(
    client, seeded_identity, task_api_headers
) -> None:
    """Task creation should derive org/user from authenticated principal, not payload ids."""
    response = client.post(
        "/v1/tasks",
        headers=task_api_headers,
        json={
            "org_id": str(uuid4()),
            "requested_by_user_id": str(uuid4()),
            "prompt": "attempt spoof",
        },
    )
    assert response.status_code == 202
    payload = response.json()
    assert payload["org_id"] == seeded_identity["org_id"]
    assert payload["requested_by_user_id"] == seeded_identity["requested_by_user_id"]


def test_create_task_idempotency_key_replays_existing_task(
    client,
    task_api_headers,
) -> None:
    """Idempotency-Key should replay existing task instead of creating duplicates."""
    first = client.post(
        "/v1/tasks",
        headers={**task_api_headers, "Idempotency-Key": "task-key-1"},
        json={"prompt": "first payload"},
    )
    assert first.status_code == 202

    second = client.post(
        "/v1/tasks",
        headers={**task_api_headers, "Idempotency-Key": "task-key-1"},
        json={"prompt": "second payload"},
    )
    assert second.status_code == 202

    first_payload = first.json()
    second_payload = second.json()
    assert second_payload["task_id"] == first_payload["task_id"]
    assert second_payload["prompt"] == "first payload"

    queued_messages = client.app.state.bus.dequeue("tasks", limit=10)
    assert len(queued_messages) == 1


def test_list_tasks_supports_pagination_and_status_filter(client, task_api_headers) -> None:
    """Task listing should be bounded and filterable."""
    created_task_ids: list[str] = []
    for index in range(3):
        response = client.post(
            "/v1/tasks",
            headers=task_api_headers,
            json={"prompt": f"task {index}"},
        )
        assert response.status_code == 202
        created_task_ids.append(response.json()["task_id"])

    canceled_task_id = created_task_ids[0]
    cancel_response = client.post(
        f"/v1/tasks/{canceled_task_id}/cancel",
        headers=task_api_headers,
    )
    assert cancel_response.status_code == 200

    page_one = client.get("/v1/tasks?limit=2&offset=0", headers=task_api_headers)
    assert page_one.status_code == 200
    assert page_one.json()["count"] == 3
    assert len(page_one.json()["items"]) == 2

    page_two = client.get("/v1/tasks?limit=2&offset=2", headers=task_api_headers)
    assert page_two.status_code == 200
    assert page_two.json()["count"] == 3
    assert len(page_two.json()["items"]) == 1

    canceled_only = client.get("/v1/tasks?status=CANCELED", headers=task_api_headers)
    assert canceled_only.status_code == 200
    assert canceled_only.json()["count"] == 1
    assert canceled_only.json()["items"][0]["task_id"] == canceled_task_id


def test_create_task_returns_503_when_queue_unavailable(client, task_api_headers) -> None:
    """Task creation returns structured error when queue enqueue fails."""

    def broken_enqueue(_queue: str, _job_id: str, _payload: dict[str, object]) -> bool:
        raise RuntimeError("redis unavailable")

    client.app.state.bus.enqueue = broken_enqueue
    response = client.post(
        "/v1/tasks",
        headers=task_api_headers,
        json={"prompt": "queue this task"},
    )
    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "TASK_QUEUE_UNAVAILABLE",
            "message": "Task enqueue failed because the queue backend is unavailable",
        }
    }
    tasks_response = client.get("/v1/tasks", headers=task_api_headers)
    assert tasks_response.status_code == 200
    assert tasks_response.json()["count"] == 1
    assert tasks_response.json()["items"][0]["status"] == "FAILED"
    assert (
        tasks_response.json()["items"][0]["error_message"]
        == "Queue backend unavailable during enqueue"
    )


def test_idempotency_replay_of_failed_task_returns_error(client, task_api_headers) -> None:
    """Replaying a failed idempotency key should return a non-2xx error."""

    def broken_enqueue(_queue: str, _job_id: str, _payload: dict[str, object]) -> bool:
        raise RuntimeError("redis unavailable")

    client.app.state.bus.enqueue = broken_enqueue
    first = client.post(
        "/v1/tasks",
        headers={**task_api_headers, "Idempotency-Key": "failed-key-1"},
        json={"prompt": "queue this task"},
    )
    assert first.status_code == 503

    second = client.post(
        "/v1/tasks",
        headers={**task_api_headers, "Idempotency-Key": "failed-key-1"},
        json={"prompt": "queue this task"},
    )
    assert second.status_code == 503
    assert second.json() == {
        "error": {
            "code": "TASK_PREVIOUS_ATTEMPT_FAILED",
            "message": (
                "A previous request with this Idempotency-Key failed. "
                "Use a new Idempotency-Key to retry."
            ),
        }
    }


def test_get_task(client, task_api_headers) -> None:
    """Created tasks can be fetched by id."""
    create_response = client.post(
        "/v1/tasks",
        headers=task_api_headers,
        json={"prompt": "draft onboarding doc"},
    )
    task_id = create_response.json()["task_id"]

    response = client.get(f"/v1/tasks/{task_id}", headers=task_api_headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == task_id
    assert payload["status"] == "QUEUED"
    assert payload["prompt"] == "draft onboarding doc"


def test_cancel_task(client, task_api_headers) -> None:
    """Cancellation updates status and completion timestamp."""
    create_response = client.post(
        "/v1/tasks",
        headers=task_api_headers,
        json={"prompt": "run dangerous command"},
    )
    task_id = create_response.json()["task_id"]

    cancel_response = client.post(f"/v1/tasks/{task_id}/cancel", headers=task_api_headers)
    assert cancel_response.status_code == 200
    cancel_payload = cancel_response.json()
    assert cancel_payload["task_id"] == task_id
    assert cancel_payload["status"] == "CANCELED"
    assert cancel_payload["completed_at"] is not None

    get_response = client.get(f"/v1/tasks/{task_id}", headers=task_api_headers)
    assert get_response.status_code == 200
    assert get_response.json()["status"] == "CANCELED"


def test_create_task_invalid_payload(client, task_api_headers) -> None:
    """Invalid create payloads are rejected by schema validation."""
    response = client.post("/v1/tasks", headers=task_api_headers, json={"prompt": ""})
    assert response.status_code == 422

    too_large = client.post(
        "/v1/tasks",
        headers=task_api_headers,
        json={"prompt": "x" * 9000},
    )
    assert too_large.status_code == 422


def test_create_task_rejects_overlong_idempotency_key(client, task_api_headers) -> None:
    """Idempotency key should be bounded to match DB column constraints."""
    response = client.post(
        "/v1/tasks",
        headers={**task_api_headers, "Idempotency-Key": "k" * 129},
        json={"prompt": "bounded key"},
    )
    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "TASK_CREATE_INVALID_IDEMPOTENCY_KEY",
            "message": "Idempotency-Key cannot exceed 128 characters",
        }
    }


def test_get_unknown_task_returns_structured_404(client, task_api_headers) -> None:
    """Unknown task ids return typed error payloads."""
    missing_id = str(uuid4())
    response = client.get(f"/v1/tasks/{missing_id}", headers=task_api_headers)
    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "TASK_NOT_FOUND",
            "message": f"Task '{missing_id}' was not found",
        }
    }


def test_get_task_rejects_invalid_uuid_path_param(client, task_api_headers) -> None:
    """Task id path params should be UUID-validated at the framework boundary."""
    response = client.get("/v1/tasks/not-a-uuid", headers=task_api_headers)
    assert response.status_code == 422


def test_cancel_unknown_task_returns_structured_404(client, task_api_headers) -> None:
    """Unknown task ids are not cancelable and return typed errors."""
    missing_id = str(uuid4())
    response = client.post(f"/v1/tasks/{missing_id}/cancel", headers=task_api_headers)
    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "TASK_NOT_FOUND",
            "message": f"Task '{missing_id}' was not found",
        }
    }


def test_cancel_task_rejects_invalid_uuid_path_param(client, task_api_headers) -> None:
    """Cancel endpoint should enforce UUID path params."""
    response = client.post("/v1/tasks/not-a-uuid/cancel", headers=task_api_headers)
    assert response.status_code == 422


def test_task_access_is_tenant_scoped(client, task_api_headers) -> None:
    """Users in one org cannot list/read/cancel tasks from another org."""
    second_identity = _create_secondary_identity(client)
    second_headers = {
        "Authorization": task_api_headers["Authorization"],
        "X-Actor-User-Id": second_identity["requested_by_user_id"],
    }

    own_task_response = client.post(
        "/v1/tasks",
        headers=task_api_headers,
        json={"prompt": "task for org A"},
    )
    assert own_task_response.status_code == 202

    second_task_response = client.post(
        "/v1/tasks",
        headers=second_headers,
        json={"prompt": "task for org B"},
    )
    assert second_task_response.status_code == 202
    second_task_id = second_task_response.json()["task_id"]

    own_list = client.get("/v1/tasks", headers=task_api_headers)
    assert own_list.status_code == 200
    assert own_list.json()["count"] == 1
    assert own_list.json()["items"][0]["prompt"] == "task for org A"

    second_list = client.get("/v1/tasks", headers=second_headers)
    assert second_list.status_code == 200
    assert second_list.json()["count"] == 1
    assert second_list.json()["items"][0]["prompt"] == "task for org B"

    assert client.get(f"/v1/tasks/{second_task_id}", headers=task_api_headers).status_code == 404
    assert (
        client.post(f"/v1/tasks/{second_task_id}/cancel", headers=task_api_headers).status_code
        == 404
    )
