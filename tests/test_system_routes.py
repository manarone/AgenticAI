from uuid import uuid4


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
    """Readiness returns healthy status when bus is initialized."""
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_readyz_not_ready_without_bus(client) -> None:
    """Readiness returns 503 when the bus is unavailable."""
    delattr(client.app.state, "bus")

    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "bus_backend": "inmemory"}


def test_readyz_not_ready_when_bus_ping_fails(client) -> None:
    """Readiness returns 503 when the queue backend reports unhealthy."""

    class UnhealthyBus:
        def ping(self) -> bool:
            return False

    client.app.state.bus = UnhealthyBus()
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "bus_backend": "inmemory"}


def test_list_tasks(client) -> None:
    """Task listing starts empty in a fresh test database."""
    response = client.get("/v1/tasks")
    assert response.status_code == 200
    assert response.json() == {"items": [], "count": 0}


def test_create_task(client, seeded_identity) -> None:
    """Task creation persists and returns lifecycle fields."""
    response = client.post(
        "/v1/tasks",
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


def test_create_task_returns_503_when_queue_unavailable(client, seeded_identity) -> None:
    """Task creation returns structured error when queue enqueue fails."""

    def broken_enqueue(_queue: str, _job_id: str, _payload: dict[str, object]) -> bool:
        raise RuntimeError("redis unavailable")

    client.app.state.bus.enqueue = broken_enqueue
    response = client.post(
        "/v1/tasks",
        json={
            **seeded_identity,
            "prompt": "queue this task",
        },
    )
    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "TASK_QUEUE_UNAVAILABLE",
            "message": "Task enqueue failed because the queue backend is unavailable",
        }
    }
    tasks_response = client.get("/v1/tasks")
    assert tasks_response.status_code == 200
    assert tasks_response.json()["count"] == 1
    assert tasks_response.json()["items"][0]["status"] == "FAILED"
    assert (
        tasks_response.json()["items"][0]["error_message"]
        == "Queue backend unavailable during enqueue"
    )


def test_get_task(client, seeded_identity) -> None:
    """Created tasks can be fetched by id."""
    create_response = client.post(
        "/v1/tasks",
        json={
            **seeded_identity,
            "prompt": "draft onboarding doc",
        },
    )
    task_id = create_response.json()["task_id"]

    response = client.get(f"/v1/tasks/{task_id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == task_id
    assert payload["status"] == "QUEUED"
    assert payload["prompt"] == "draft onboarding doc"


def test_cancel_task(client, seeded_identity) -> None:
    """Cancellation updates status and completion timestamp."""
    create_response = client.post(
        "/v1/tasks",
        json={
            **seeded_identity,
            "prompt": "run dangerous command",
        },
    )
    task_id = create_response.json()["task_id"]

    cancel_response = client.post(f"/v1/tasks/{task_id}/cancel")
    assert cancel_response.status_code == 200
    cancel_payload = cancel_response.json()
    assert cancel_payload["task_id"] == task_id
    assert cancel_payload["status"] == "CANCELED"
    assert cancel_payload["completed_at"] is not None

    get_response = client.get(f"/v1/tasks/{task_id}")
    assert get_response.status_code == 200
    assert get_response.json()["status"] == "CANCELED"


def test_create_task_invalid_payload(client) -> None:
    """Invalid create payloads are rejected by schema validation."""
    response = client.post("/v1/tasks", json={"prompt": ""})
    assert response.status_code == 422


def test_create_task_unknown_reference_returns_structured_400(client) -> None:
    """Unknown org/user references return typed validation errors."""
    response = client.post(
        "/v1/tasks",
        json={
            "org_id": str(uuid4()),
            "requested_by_user_id": str(uuid4()),
            "prompt": "do work",
        },
    )
    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "TASK_CREATE_INVALID_REFERENCE",
            "message": "org_id or requested_by_user_id does not exist",
        }
    }


def test_get_unknown_task_returns_structured_404(client) -> None:
    """Unknown task ids return typed error payloads."""
    missing_id = str(uuid4())
    response = client.get(f"/v1/tasks/{missing_id}")
    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "TASK_NOT_FOUND",
            "message": f"Task '{missing_id}' was not found",
        }
    }


def test_cancel_unknown_task_returns_structured_404(client) -> None:
    """Unknown task ids are not cancelable and return typed errors."""
    missing_id = str(uuid4())
    response = client.post(f"/v1/tasks/{missing_id}/cancel")
    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "TASK_NOT_FOUND",
            "message": f"Task '{missing_id}' was not found",
        }
    }
