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


def test_list_tasks(client) -> None:
    """Task listing starts empty in the scaffold."""
    response = client.get("/v1/tasks")
    assert response.status_code == 200
    assert response.json()["count"] == 0


def test_create_task(client) -> None:
    """Task creation returns a queued placeholder task payload."""
    response = client.post("/v1/tasks")
    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "QUEUED"
    assert payload["task_id"]
