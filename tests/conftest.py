from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agenticai.core.config import get_settings
from agenticai.main import create_app
from tests.db_seed import seed_identity_database
from tests.jwt_utils import make_task_api_jwt

TEST_ORG_ID = "00000000-0000-0000-0000-000000000001"
TEST_USER_ID = "00000000-0000-0000-0000-000000000002"
TEST_TASK_API_JWT_SECRET = "test-task-api-jwt-secret-00000001"
TEST_TASK_API_JWT_AUDIENCE = "agenticai-task-api-tests"


@pytest.fixture
def seeded_identity() -> dict[str, str]:
    """Static identity references inserted in test databases."""
    return {
        "org_id": TEST_ORG_ID,
        "requested_by_user_id": TEST_USER_ID,
    }


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Generator[TestClient, None, None]:
    """Provide a fresh test client and isolated DB for each test case."""
    database_url = f"sqlite:///{tmp_path}/test.db"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setenv("TASK_API_JWT_SECRET", TEST_TASK_API_JWT_SECRET)
    monkeypatch.setenv("TASK_API_JWT_AUDIENCE", TEST_TASK_API_JWT_AUDIENCE)
    get_settings.cache_clear()

    seed_identity_database(
        database_url,
        org_id=TEST_ORG_ID,
        org_slug="test-org",
        org_name="Test Org",
        user_id=TEST_USER_ID,
        telegram_user_id=123456789,
        display_name="Tester",
    )

    with TestClient(create_app(start_coordinator=False)) as test_client:
        yield test_client
    get_settings.cache_clear()


@pytest.fixture
def task_api_headers() -> dict[str, str]:
    """Authenticated task API headers for the seeded test user."""
    token = make_task_api_jwt(
        secret=TEST_TASK_API_JWT_SECRET,
        audience=TEST_TASK_API_JWT_AUDIENCE,
        sub=TEST_USER_ID,
        org_id=TEST_ORG_ID,
    )
    return {
        "Authorization": f"Bearer {token}",
    }
