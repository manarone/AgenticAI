from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agenticai.bus.base import TASK_QUEUE
from agenticai.db.models import Organization, Task, TelegramWebhookEvent, User

WEBHOOK_PATH = "/telegram/webhook"
WEBHOOK_SECRET_HEADER = {"X-Telegram-Bot-Api-Secret-Token": "test-webhook-secret"}


def _message_update(*, update_id: int, telegram_user_id: int, text: str) -> dict[str, object]:
    """Build a minimal Telegram message update payload."""
    return {
        "update_id": update_id,
        "message": {
            "text": text,
            "from": {
                "id": telegram_user_id,
                "first_name": "Unit",
                "last_name": "Tester",
                "username": "unit.tester",
            },
        },
    }


def test_webhook_rejects_missing_secret(client) -> None:
    """Webhook rejects requests without the configured Telegram secret header."""
    response = client.post(
        WEBHOOK_PATH,
        json=_message_update(update_id=1001, telegram_user_id=123456789, text="hello"),
    )
    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "TELEGRAM_WEBHOOK_UNAUTHORIZED",
            "message": "Invalid Telegram webhook secret",
        }
    }


def test_webhook_rejects_invalid_secret(client) -> None:
    """Webhook rejects requests with an invalid Telegram secret header."""
    response = client.post(
        WEBHOOK_PATH,
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
        json=_message_update(update_id=1002, telegram_user_id=123456789, text="hello"),
    )
    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "TELEGRAM_WEBHOOK_UNAUTHORIZED",
            "message": "Invalid Telegram webhook secret",
        }
    }


def test_webhook_returns_503_when_secret_not_configured_and_insecure_not_allowed(client) -> None:
    """Webhook should fail closed when secret is missing and insecure mode is disabled."""
    client.app.state.settings.telegram_webhook_secret = None
    client.app.state.settings.allow_insecure_telegram_webhook = False
    response = client.post(
        WEBHOOK_PATH,
        json=_message_update(update_id=1003, telegram_user_id=123456789, text="hello"),
    )
    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "TELEGRAM_WEBHOOK_MISCONFIGURED",
            "message": (
                "TELEGRAM_WEBHOOK_SECRET is required unless ALLOW_INSECURE_TELEGRAM_WEBHOOK=true"
            ),
        }
    }


def test_webhook_is_idempotent_for_duplicate_delivery(client, seeded_identity) -> None:
    """Same Telegram update_id must not enqueue duplicate tasks."""
    payload = _message_update(update_id=2001, telegram_user_id=123456789, text="plan release")

    first = client.post(WEBHOOK_PATH, headers=WEBHOOK_SECRET_HEADER, json=payload)
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["ok"] is True
    assert first_payload["status"] == "accepted"
    assert first_payload["duplicate"] is False
    assert first_payload["task_id"]

    second = client.post(WEBHOOK_PATH, headers=WEBHOOK_SECRET_HEADER, json=payload)
    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["ok"] is True
    assert second_payload["status"] == "accepted"
    assert second_payload["duplicate"] is True
    assert second_payload["task_id"] == first_payload["task_id"]

    with Session(bind=client.app.state.db_engine) as session:
        task_count = session.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.org_id == seeded_identity["org_id"], Task.prompt == "plan release")
        )
    assert task_count == 1

    queued_messages = client.app.state.bus.dequeue(TASK_QUEUE, limit=10)
    assert len(queued_messages) == 1
    assert queued_messages[0]["job_id"] == first_payload["task_id"]
    assert queued_messages[0]["payload"]["task_id"] == first_payload["task_id"]
    assert (
        queued_messages[0]["payload"]["requested_by_user_id"]
        == seeded_identity["requested_by_user_id"]
    )


def test_webhook_start_with_invite_registers_user(client) -> None:
    """Unknown users can be linked to an org via '/start <org_slug>'."""
    new_org_id = str(uuid4())
    with Session(bind=client.app.state.db_engine) as session:
        session.add(Organization(id=new_org_id, slug="invite-org", name="Invite Org"))
        session.commit()

    telegram_user_id = 987654321
    response = client.post(
        WEBHOOK_PATH,
        headers=WEBHOOK_SECRET_HEADER,
        json=_message_update(
            update_id=3001,
            telegram_user_id=telegram_user_id,
            text="/start invite-org",
        ),
    )
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "status": "registered",
        "update_id": 3001,
        "duplicate": False,
        "task_id": None,
    }

    with Session(bind=client.app.state.db_engine) as session:
        user = session.execute(
            select(User).where(User.org_id == new_org_id, User.telegram_user_id == telegram_user_id)
        ).scalar_one_or_none()
    assert user is not None

    queued_messages = client.app.state.bus.dequeue(TASK_QUEUE, limit=10)
    assert queued_messages == []


def test_webhook_start_does_not_reassign_existing_telegram_user_to_new_org(client) -> None:
    """Existing Telegram users remain bound to their original org even with a new invite code."""
    second_org_id = str(uuid4())
    with Session(bind=client.app.state.db_engine) as session:
        session.add(Organization(id=second_org_id, slug="other-org", name="Other Org"))
        session.commit()

    response = client.post(
        WEBHOOK_PATH,
        headers=WEBHOOK_SECRET_HEADER,
        json=_message_update(
            update_id=3002,
            telegram_user_id=123456789,
            text="/start other-org",
        ),
    )
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "status": "ignored",
        "update_id": 3002,
        "duplicate": False,
        "task_id": None,
    }

    with Session(bind=client.app.state.db_engine) as session:
        users = (
            session.execute(select(User).where(User.telegram_user_id == 123456789)).scalars().all()
        )
    assert len(users) == 1


def test_webhook_unknown_user_without_invite_requires_registration(client) -> None:
    """Unknown users without invite context are acknowledged but not queued."""
    response = client.post(
        WEBHOOK_PATH,
        headers=WEBHOOK_SECRET_HEADER,
        json=_message_update(update_id=4001, telegram_user_id=999999999, text="do thing"),
    )
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "status": "registration_required",
        "update_id": 4001,
        "duplicate": False,
        "task_id": None,
    }

    queued_messages = client.app.state.bus.dequeue(TASK_QUEUE, limit=10)
    assert queued_messages == []


def test_webhook_returns_503_when_queue_unavailable(client) -> None:
    """Webhook returns a typed 503 and persists failed status when enqueue fails."""

    def broken_enqueue(_queue: str, _job_id: str, _payload: dict[str, object]) -> bool:
        raise RuntimeError("queue backend unavailable")

    client.app.state.bus.enqueue = broken_enqueue
    response = client.post(
        WEBHOOK_PATH,
        headers=WEBHOOK_SECRET_HEADER,
        json=_message_update(update_id=5001, telegram_user_id=123456789, text="do work"),
    )
    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "TASK_QUEUE_UNAVAILABLE",
            "message": "Task enqueue failed because the queue backend is unavailable",
        }
    }

    with Session(bind=client.app.state.db_engine) as session:
        task = session.execute(select(Task).where(Task.prompt == "do work")).scalar_one()
        event = session.execute(
            select(TelegramWebhookEvent).where(TelegramWebhookEvent.update_id == 5001)
        ).scalar_one()
    assert task.status == "FAILED"
    assert task.error_message == "Queue backend unavailable during enqueue"
    assert task.completed_at is not None
    assert event.outcome == "ENQUEUE_FAILED"

    duplicate = client.post(
        WEBHOOK_PATH,
        headers=WEBHOOK_SECRET_HEADER,
        json=_message_update(update_id=5001, telegram_user_id=123456789, text="do work"),
    )
    assert duplicate.status_code == 503
    assert duplicate.json() == {
        "error": {
            "code": "TASK_QUEUE_UNAVAILABLE",
            "message": "Task enqueue failed because the queue backend is unavailable",
        }
    }


def test_webhook_duplicate_recovers_previous_enqueue_failure(client) -> None:
    """Duplicate delivery should recover prior ENQUEUE_FAILED outcomes when queue returns."""

    def broken_enqueue(_queue: str, _job_id: str, _payload: dict[str, object]) -> bool:
        raise RuntimeError("queue backend unavailable")

    original_enqueue = client.app.state.bus.enqueue
    client.app.state.bus.enqueue = broken_enqueue
    update_payload = _message_update(update_id=5002, telegram_user_id=123456789, text="retry me")
    first = client.post(
        WEBHOOK_PATH,
        headers=WEBHOOK_SECRET_HEADER,
        json=update_payload,
    )
    assert first.status_code == 503

    client.app.state.bus.enqueue = original_enqueue
    duplicate = client.post(
        WEBHOOK_PATH,
        headers=WEBHOOK_SECRET_HEADER,
        json=update_payload,
    )
    assert duplicate.status_code == 200
    duplicate_payload = duplicate.json()
    assert duplicate_payload["ok"] is True
    assert duplicate_payload["status"] == "accepted"
    assert duplicate_payload["duplicate"] is True
    assert duplicate_payload["task_id"] is not None

    with Session(bind=client.app.state.db_engine) as session:
        task = session.get(Task, duplicate_payload["task_id"])
        assert task is not None
        assert task.status == "QUEUED"
        assert task.error_message is None
        assert task.completed_at is None

        event = session.execute(
            select(TelegramWebhookEvent).where(TelegramWebhookEvent.update_id == 5002)
        ).scalar_one()
        assert event.outcome == "TASK_ENQUEUED"

    queued_messages = client.app.state.bus.dequeue(TASK_QUEUE, limit=10)
    assert len(queued_messages) == 1
    assert queued_messages[0]["job_id"] == duplicate_payload["task_id"]
