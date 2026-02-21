def test_root(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "AgenticAI"
    assert payload["status"] == "ok"


def test_healthz(client) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz(client) -> None:
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_list_tasks(client) -> None:
    response = client.get("/v1/tasks")
    assert response.status_code == 200
    assert response.json()["count"] == 0


def test_create_task(client) -> None:
    response = client.post("/v1/tasks")
    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "QUEUED"
    assert payload["task_id"]
