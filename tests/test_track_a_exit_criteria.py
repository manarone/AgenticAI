import time
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agenticai.core.config import get_settings
from agenticai.db.base import Base
from agenticai.db.models import Organization, Task, TelegramWebhookEvent, User
from agenticai.db.session import build_engine
from agenticai.main import create_app

WEBHOOK_PATH = "/telegram/webhook"
WEBHOOK_SECRET_HEADER = {"X-Telegram-Bot-Api-Secret-Token": "track-a-secret"}

TRACK_A_ORG_ID = "00000000-0000-0000-0000-000000000101"
TRACK_A_USER_ID = "00000000-0000-0000-0000-000000000102"
TRACK_A_TELEGRAM_USER_ID = 222333444
TRACK_A_TASK_API_AUTH_TOKEN = "track-a-task-api-token"
TRACK_A_TASK_HEADERS = {
    "Authorization": f"Bearer {TRACK_A_TASK_API_AUTH_TOKEN}",
    "X-Actor-User-Id": TRACK_A_USER_ID,
}


@pytest.fixture
def track_a_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Generator[TestClient, None, None]:
    database_url = f"sqlite:///{tmp_path}/track-a-exit.db"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "track-a-secret")
    monkeypatch.setenv("COORDINATOR_POLL_INTERVAL_SECONDS", "0.01")
    monkeypatch.setenv("COORDINATOR_BATCH_SIZE", "10")
    monkeypatch.setenv("BUS_BACKEND", "inmemory")
    monkeypatch.setenv("TASK_API_AUTH_TOKEN", TRACK_A_TASK_API_AUTH_TOKEN)
    get_settings.cache_clear()

    engine = build_engine(database_url)
    Base.metadata.create_all(bind=engine)
    with Session(bind=engine) as session:
        session.add(
            Organization(
                id=TRACK_A_ORG_ID,
                slug="track-a-org",
                name="Track A Org",
            )
        )
        session.add(
            User(
                id=TRACK_A_USER_ID,
                org_id=TRACK_A_ORG_ID,
                telegram_user_id=TRACK_A_TELEGRAM_USER_ID,
                display_name="Track A User",
            )
        )
        session.commit()
    engine.dispose()

    with TestClient(create_app(start_coordinator=True)) as client:
        yield client
    get_settings.cache_clear()


def _message_update(*, update_id: int, telegram_user_id: int, text: str) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "text": text,
            "from": {
                "id": telegram_user_id,
                "first_name": "Track",
                "last_name": "User",
                "username": "track.user",
            },
        },
    }


def _wait_for_terminal_status(
    client: TestClient,
    task_id: str,
    *,
    timeout_seconds: float = 5.0,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    last_payload: dict[str, object] | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/v1/tasks/{task_id}", headers=TRACK_A_TASK_HEADERS)
        assert response.status_code == 200
        payload = response.json()
        last_payload = payload
        if payload["status"] in {"SUCCEEDED", "FAILED", "CANCELED", "TIMED_OUT"}:
            return payload
        time.sleep(0.02)
    pytest.fail(
        f"Timed out waiting for terminal status for task {task_id}; "
        f"last status={last_payload['status'] if last_payload else 'unknown'}"
    )


def test_track_a_exit_criteria_end_to_end(track_a_client: TestClient) -> None:
    """Telegram ingress should be deterministic and lifecycle state should persist."""
    payload = _message_update(
        update_id=9001,
        telegram_user_id=TRACK_A_TELEGRAM_USER_ID,
        text="prepare release runbook",
    )

    first = track_a_client.post(WEBHOOK_PATH, headers=WEBHOOK_SECRET_HEADER, json=payload)
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["ok"] is True
    assert first_payload["status"] == "accepted"
    assert first_payload["update_id"] == 9001
    assert first_payload["duplicate"] is False
    assert isinstance(first_payload["task_id"], str) and first_payload["task_id"]

    second = track_a_client.post(WEBHOOK_PATH, headers=WEBHOOK_SECRET_HEADER, json=payload)
    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload == {
        "ok": True,
        "status": "accepted",
        "update_id": 9001,
        "duplicate": True,
        "task_id": first_payload["task_id"],
    }

    terminal_payload = _wait_for_terminal_status(track_a_client, first_payload["task_id"])
    assert terminal_payload["status"] == "SUCCEEDED"
    assert terminal_payload["started_at"] is not None
    assert terminal_payload["completed_at"] is not None
    assert terminal_payload["error_message"] is None

    with Session(bind=track_a_client.app.state.db_engine) as session:
        task_count = session.scalar(
            select(func.count()).select_from(Task).where(Task.id == first_payload["task_id"])
        )
        event = session.execute(
            select(TelegramWebhookEvent).where(TelegramWebhookEvent.update_id == 9001)
        ).scalar_one_or_none()
    assert task_count == 1
    assert event is not None
    assert event.task_id == first_payload["task_id"]
    assert event.outcome == "TASK_ENQUEUED"
