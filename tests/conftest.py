from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from agenticai.main import create_app


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """Provide a fresh test client for each test case."""
    with TestClient(create_app()) as test_client:
        yield test_client
