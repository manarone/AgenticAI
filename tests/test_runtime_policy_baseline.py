from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agenticai.core.config import get_settings
from agenticai.main import create_app
from tests.db_seed import seed_identity_database
from tests.jwt_utils import make_task_api_jwt

TEST_ORG_ID = "00000000-0000-0000-0000-000000000901"
TEST_USER_ID = "00000000-0000-0000-0000-000000000902"
TEST_TASK_API_JWT_SECRET = "policy-test-jwt-secret-000000002"
TEST_TASK_API_JWT_AUDIENCE = "agenticai-policy-tests"
TEST_WEBHOOK_SECRET = "policy-webhook-secret"


@pytest.fixture
def rate_limited_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Generator[TestClient, None, None]:
    """Provide an app with aggressive request limits to validate rate-limit behavior."""
    database_url = f"sqlite:///{tmp_path}/policy-limits.db"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", TEST_WEBHOOK_SECRET)
    monkeypatch.setenv("TASK_API_JWT_SECRET", TEST_TASK_API_JWT_SECRET)
    monkeypatch.setenv("TASK_API_JWT_AUDIENCE", TEST_TASK_API_JWT_AUDIENCE)
    monkeypatch.setenv("ENABLE_RATE_LIMITING", "true")
    monkeypatch.setenv("TASK_CREATE_RATE_LIMIT_REQUESTS", "1")
    monkeypatch.setenv("TASK_CREATE_RATE_LIMIT_WINDOW_SECONDS", "60")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_RATE_LIMIT_REQUESTS", "1")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_RATE_LIMIT_WINDOW_SECONDS", "60")
    get_settings.cache_clear()

    seed_identity_database(
        database_url,
        org_id=TEST_ORG_ID,
        org_slug="policy-test-org",
        org_name="Policy Test Org",
        user_id=TEST_USER_ID,
        telegram_user_id=123456789,
        display_name="Policy Tester",
    )

    with TestClient(create_app(start_coordinator=False)) as client:
        yield client
    get_settings.cache_clear()


def test_docs_disabled_outside_local_dev_test(monkeypatch: pytest.MonkeyPatch) -> None:
    """Swagger/ReDoc should be unavailable in production-like environments."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/agenticai")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "secret")
    monkeypatch.setenv("TASK_API_JWT_SECRET", "secret")
    monkeypatch.setenv("TASK_API_JWT_AUDIENCE", "agenticai-prod")
    get_settings.cache_clear()
    try:
        app = create_app(start_coordinator=False)
        assert app.docs_url is None
        assert app.redoc_url is None
        assert app.openapi_url is None
    finally:
        get_settings.cache_clear()


def test_docs_enabled_in_test_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test environment should keep docs enabled for local iteration."""
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./docs-test.db")
    get_settings.cache_clear()
    try:
        app = create_app(start_coordinator=False)
        assert app.docs_url == "/docs"
        assert app.redoc_url == "/redoc"
        assert app.openapi_url == "/openapi.json"
    finally:
        get_settings.cache_clear()


def test_create_app_rejects_sqlite_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production startup must fail fast when DATABASE_URL points to SQLite."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./agenticai.db")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "secret")
    monkeypatch.setenv("TASK_API_JWT_SECRET", "secret")
    monkeypatch.setenv("TASK_API_JWT_AUDIENCE", "agenticai-prod")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValueError, match="DATABASE_URL must not use sqlite"):
            create_app(start_coordinator=False)
    finally:
        get_settings.cache_clear()


def test_task_create_rate_limit_enforced(rate_limited_client: TestClient) -> None:
    """Task creation should return typed 429s once the configured limit is exceeded."""
    token = make_task_api_jwt(
        secret=TEST_TASK_API_JWT_SECRET,
        audience=TEST_TASK_API_JWT_AUDIENCE,
        sub=TEST_USER_ID,
        org_id=TEST_ORG_ID,
    )
    headers = {
        "Authorization": f"Bearer {token}",
    }
    first = rate_limited_client.post("/v1/tasks", headers=headers, json={"prompt": "first"})
    assert first.status_code == 202

    second = rate_limited_client.post("/v1/tasks", headers=headers, json={"prompt": "second"})
    assert second.status_code == 429
    assert second.json() == {
        "error": {
            "code": "TASK_CREATE_RATE_LIMITED",
            "message": "Too many task creation requests",
        }
    }
    assert second.headers.get("Retry-After") is not None


def test_telegram_webhook_rate_limit_enforced(rate_limited_client: TestClient) -> None:
    """Webhook ingress should return typed 429s when the endpoint exceeds policy."""
    first = rate_limited_client.post(
        "/telegram/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": TEST_WEBHOOK_SECRET},
        json={
            "update_id": 8101,
            "message": {
                "text": "prepare release",
                "from": {"id": 123456789},
            },
        },
    )
    assert first.status_code == 200

    second = rate_limited_client.post(
        "/telegram/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": TEST_WEBHOOK_SECRET},
        json={
            "update_id": 8102,
            "message": {
                "text": "prepare rollback",
                "from": {"id": 123456789},
            },
        },
    )
    assert second.status_code == 429
    assert second.json() == {
        "error": {
            "code": "TELEGRAM_WEBHOOK_RATE_LIMITED",
            "message": "Too many Telegram webhook requests",
        }
    }
    assert second.headers.get("Retry-After") is not None
