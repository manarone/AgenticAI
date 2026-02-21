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


def test_task_endpoints(client) -> None:
    list_response = client.get("/v1/tasks")
    assert list_response.status_code == 200
    assert list_response.json()["count"] == 0

    create_response = client.post("/v1/tasks")
    assert create_response.status_code == 202
    payload = create_response.json()
    assert payload["status"] == "QUEUED"
    assert payload["task_id"]
