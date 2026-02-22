"""Telegram webhook ingress route."""

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agenticai.api.dependencies import get_db_session
from agenticai.api.schemas.tasks import ErrorResponse
from agenticai.api.schemas.telegram import TelegramMessage, TelegramUpdate, TelegramWebhookAck
from agenticai.bus.base import TASK_QUEUE
from agenticai.core.config import get_settings
from agenticai.core.observability import log_event
from agenticai.db.models import (
    Organization,
    Task,
    TaskStatus,
    TelegramWebhookEvent,
    TelegramWebhookOutcome,
    User,
)

router = APIRouter(prefix="/telegram", tags=["telegram"])
DBSession = Annotated[Session, Depends(get_db_session)]
logger = logging.getLogger(__name__)


def _error_response(*, status_code: int, code: str, message: str) -> JSONResponse:
    """Build a structured error payload."""
    payload = ErrorResponse.model_validate(
        {
            "error": {
                "code": code,
                "message": message,
            }
        }
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


def _ack_status(outcome: str) -> str:
    """Convert persisted outcome enum into response status string."""
    mapping = {
        TelegramWebhookOutcome.TASK_ENQUEUED.value: "accepted",
        TelegramWebhookOutcome.ENQUEUE_FAILED.value: "failed",
        TelegramWebhookOutcome.REGISTERED.value: "registered",
        TelegramWebhookOutcome.REGISTRATION_REQUIRED.value: "registration_required",
        TelegramWebhookOutcome.IGNORED.value: "ignored",
    }
    return mapping.get(outcome, outcome.lower())


def _build_ack(event: TelegramWebhookEvent, *, duplicate: bool) -> TelegramWebhookAck:
    """Build webhook acknowledgement from persisted event state."""
    return TelegramWebhookAck(
        status=_ack_status(event.outcome),
        update_id=event.update_id,
        duplicate=duplicate,
        task_id=event.task_id,
    )


def _enqueue_failed_response() -> JSONResponse:
    """Build queue failure response for deterministic retries."""
    return _error_response(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="TASK_QUEUE_UNAVAILABLE",
        message="Task enqueue failed because the queue backend is unavailable",
    )


def _response_for_existing_event(
    *,
    payload: TelegramUpdate,
    event: TelegramWebhookEvent,
    duplicate: bool,
) -> TelegramWebhookAck | JSONResponse:
    """Replay a deterministic response from persisted webhook outcome."""
    ack = _build_ack(event, duplicate=duplicate)
    _log_webhook_outcome(
        payload=payload,
        outcome=ack.status,
        duplicate=ack.duplicate,
        telegram_user_id=event.telegram_user_id,
        task_id=event.task_id,
    )
    if event.outcome == TelegramWebhookOutcome.ENQUEUE_FAILED.value:
        return _enqueue_failed_response()
    return ack


def _log_webhook_outcome(
    *,
    payload: TelegramUpdate,
    outcome: str,
    duplicate: bool,
    telegram_user_id: int | None,
    task_id: str | None,
) -> None:
    """Emit one structured webhook lifecycle event."""
    log_event(
        logger,
        event="telegram.webhook.ack",
        update_id=payload.update_id,
        outcome=outcome,
        duplicate=duplicate,
        telegram_user_id=telegram_user_id,
        task_id=task_id,
    )


def _parse_invite_code(text: str | None) -> str | None:
    """Parse '/start <invite_code>' and return invite code when present."""
    if not text:
        return None
    trimmed = text.strip()
    if not trimmed.startswith("/start"):
        return None
    parts = trimmed.split(maxsplit=1)
    if len(parts) != 2:
        return None
    return parts[1].strip().lower() or None


def _message_from_update(payload: TelegramUpdate) -> TelegramMessage | None:
    """Extract the first supported message body from a Telegram update."""
    if payload.message is not None:
        return payload.message
    if payload.edited_message is not None:
        return payload.edited_message
    return None


def _display_name_from_message(message: TelegramMessage) -> str | None:
    """Build a stable display name from Telegram identity fields."""
    from_user = message.from_user
    if from_user is None:
        return None

    pieces = [from_user.first_name, from_user.last_name]
    name = " ".join(piece.strip() for piece in pieces if piece and piece.strip())
    if name:
        return name
    if from_user.username and from_user.username.strip():
        return from_user.username.strip()
    return None


def _store_event(
    db: Session,
    *,
    update_id: int,
    telegram_user_id: int | None,
    message_text: str | None,
    outcome: TelegramWebhookOutcome,
    task_id: str | None = None,
) -> tuple[TelegramWebhookEvent, bool]:
    """Persist webhook event for idempotent replay handling."""
    event = TelegramWebhookEvent(
        update_id=update_id,
        telegram_user_id=telegram_user_id,
        message_text=message_text,
        outcome=outcome.value,
        task_id=task_id,
    )
    db.add(event)
    try:
        db.commit()
        db.refresh(event)
        return event, False
    except IntegrityError:
        db.rollback()
        existing_event = db.execute(
            select(TelegramWebhookEvent).where(TelegramWebhookEvent.update_id == update_id)
        ).scalar_one_or_none()
        if existing_event is not None:
            return existing_event, True
        raise


@router.post(
    "/webhook",
    response_model=TelegramWebhookAck,
    responses={
        401: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def telegram_webhook(
    payload: TelegramUpdate,
    request: Request,
    db: DBSession,
    webhook_secret: Annotated[str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")] = None,
) -> TelegramWebhookAck | JSONResponse:
    """Process incoming Telegram webhook updates."""
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        settings = get_settings()

    expected_secret = settings.telegram_webhook_secret
    if expected_secret is None:
        logger.warning(
            "TELEGRAM_WEBHOOK_SECRET is not configured; /telegram/webhook is unauthenticated. "
            "Set TELEGRAM_WEBHOOK_SECRET to secure webhook ingress."
        )
    elif webhook_secret != expected_secret.get_secret_value():
        return _error_response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="TELEGRAM_WEBHOOK_UNAUTHORIZED",
            message="Invalid Telegram webhook secret",
        )

    existing_event = db.execute(
        select(TelegramWebhookEvent).where(TelegramWebhookEvent.update_id == payload.update_id)
    ).scalar_one_or_none()
    if existing_event is not None:
        return _response_for_existing_event(
            payload=payload,
            event=existing_event,
            duplicate=True,
        )

    message = _message_from_update(payload)
    if message is None or message.from_user is None:
        event, duplicate = _store_event(
            db,
            update_id=payload.update_id,
            telegram_user_id=None,
            message_text=None,
            outcome=TelegramWebhookOutcome.IGNORED,
        )
        ack = _build_ack(event, duplicate=duplicate)
        _log_webhook_outcome(
            payload=payload,
            outcome=ack.status,
            duplicate=ack.duplicate,
            telegram_user_id=event.telegram_user_id,
            task_id=event.task_id,
        )
        return ack

    telegram_user_id = message.from_user.id
    message_text = message.text.strip() if message.text else None

    user = db.execute(
        select(User)
        .where(User.telegram_user_id == telegram_user_id)
        .order_by(User.created_at.asc())
        .limit(1)
    ).scalar_one_or_none()

    invite_code = _parse_invite_code(message_text)
    if user is None and invite_code:
        org = db.execute(
            select(Organization).where(Organization.slug == invite_code).limit(1)
        ).scalar_one_or_none()
        if org is not None:
            user = User(
                org_id=org.id,
                telegram_user_id=telegram_user_id,
                display_name=_display_name_from_message(message),
            )
            event = TelegramWebhookEvent(
                update_id=payload.update_id,
                telegram_user_id=telegram_user_id,
                message_text=message_text,
                outcome=TelegramWebhookOutcome.REGISTERED.value,
            )
            db.add(user)
            db.add(event)
            try:
                db.commit()
                db.refresh(user)
                db.refresh(event)
            except IntegrityError:
                db.rollback()
                existing_event = db.execute(
                    select(TelegramWebhookEvent).where(
                        TelegramWebhookEvent.update_id == payload.update_id
                    )
                ).scalar_one_or_none()
                if existing_event is not None:
                    return _response_for_existing_event(
                        payload=payload,
                        event=existing_event,
                        duplicate=True,
                    )
                raise
            ack = _build_ack(event, duplicate=False)
            _log_webhook_outcome(
                payload=payload,
                outcome=ack.status,
                duplicate=ack.duplicate,
                telegram_user_id=event.telegram_user_id,
                task_id=event.task_id,
            )
            return ack

    if user is None:
        event, duplicate = _store_event(
            db,
            update_id=payload.update_id,
            telegram_user_id=telegram_user_id,
            message_text=message_text,
            outcome=TelegramWebhookOutcome.REGISTRATION_REQUIRED,
        )
        ack = _build_ack(event, duplicate=duplicate)
        _log_webhook_outcome(
            payload=payload,
            outcome=ack.status,
            duplicate=ack.duplicate,
            telegram_user_id=event.telegram_user_id,
            task_id=event.task_id,
        )
        return ack

    if not message_text or message_text.startswith("/start"):
        event, duplicate = _store_event(
            db,
            update_id=payload.update_id,
            telegram_user_id=telegram_user_id,
            message_text=message_text,
            outcome=TelegramWebhookOutcome.IGNORED,
        )
        ack = _build_ack(event, duplicate=duplicate)
        _log_webhook_outcome(
            payload=payload,
            outcome=ack.status,
            duplicate=ack.duplicate,
            telegram_user_id=event.telegram_user_id,
            task_id=event.task_id,
        )
        return ack

    bus = getattr(request.app.state, "bus", None)
    if bus is None:
        return _error_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="TASK_QUEUE_UNAVAILABLE",
            message="Task queue bus is unavailable",
        )

    now = datetime.now(UTC)
    task = Task(
        org_id=user.org_id,
        requested_by_user_id=user.id,
        status=TaskStatus.QUEUED.value,
        prompt=message_text,
        created_at=now,
        updated_at=now,
    )
    db.add(task)
    try:
        db.flush()
        event = TelegramWebhookEvent(
            update_id=payload.update_id,
            telegram_user_id=telegram_user_id,
            message_text=message_text,
            outcome=TelegramWebhookOutcome.TASK_ENQUEUED.value,
            task_id=task.id,
        )
        db.add(event)
        db.commit()
        db.refresh(event)
    except IntegrityError:
        db.rollback()
        existing_event = db.execute(
            select(TelegramWebhookEvent).where(TelegramWebhookEvent.update_id == payload.update_id)
        ).scalar_one_or_none()
        if existing_event is not None:
            return _response_for_existing_event(
                payload=payload,
                event=existing_event,
                duplicate=True,
            )
        raise

    try:
        accepted = bus.enqueue(
            TASK_QUEUE,
            task.id,
            {
                "task_id": task.id,
                "org_id": task.org_id,
                "requested_by_user_id": task.requested_by_user_id,
                "status": task.status,
                "source": "telegram",
                "telegram_update_id": payload.update_id,
            },
        )
    except Exception:
        accepted = False
        logger.exception("Failed to enqueue task %s from Telegram webhook", task.id)

    if not accepted:
        failure_time = datetime.now(UTC)
        task.status = TaskStatus.FAILED.value
        task.error_message = "Queue backend unavailable during enqueue"
        task.completed_at = failure_time
        task.updated_at = failure_time
        event.outcome = TelegramWebhookOutcome.ENQUEUE_FAILED.value
        db.add(task)
        db.add(event)
        db.commit()
        log_event(
            logger,
            event="telegram.webhook.enqueue_failed",
            update_id=payload.update_id,
            task_id=task.id,
            telegram_user_id=telegram_user_id,
            queue=TASK_QUEUE,
        )
        return _enqueue_failed_response()

    log_event(
        logger,
        event="telegram.webhook.task_enqueued",
        update_id=payload.update_id,
        task_id=task.id,
        telegram_user_id=telegram_user_id,
        queue=TASK_QUEUE,
    )
    ack = _build_ack(event, duplicate=False)
    _log_webhook_outcome(
        payload=payload,
        outcome=ack.status,
        duplicate=ack.duplicate,
        telegram_user_id=event.telegram_user_id,
        task_id=event.task_id,
    )
    return ack
