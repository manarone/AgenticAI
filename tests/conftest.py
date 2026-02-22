from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from agenticai.core.config import get_settings
from agenticai.db.base import Base
from agenticai.db.models import Organization, User
from agenticai.db.session import build_engine
from agenticai.main import create_app

TEST_ORG_ID = "00000000-0000-0000-0000-000000000001"
TEST_USER_ID = "00000000-0000-0000-0000-000000000002"


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
    get_settings.cache_clear()

    engine = build_engine(database_url)
    Base.metadata.create_all(bind=engine)
    with Session(bind=engine) as session:
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
                display_name="Tester",
            )
        )
        session.commit()
    engine.dispose()

    with TestClient(create_app(start_coordinator=False)) as test_client:
        yield test_client
    get_settings.cache_clear()
