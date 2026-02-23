import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agenticai.api.dependencies import (
    TaskApiPrincipal,
    get_db_session,
    get_event_bus,
    get_task_api_principal,
)
from agenticai.api.responses import build_error_response
from agenticai.api.schemas.tasks import (
    ErrorResponse,
    TaskCreateRequest,
    TaskListResponse,
    TaskResponse,
)
from agenticai.bus.base import TASK_QUEUE, EventBus
from agenticai.bus.exceptions import QUEUE_EXCEPTIONS
from agenticai.core.observability import log_event
from agenticai.db.models import Task, TaskStatus

router = APIRouter(prefix="/v1", tags=["v1"])
DBSession = Annotated[Session, Depends(get_db_session)]
EventBusDep = Annotated[EventBus, Depends(get_event_bus)]
logger = logging.getLogger(__name__)


TERMINAL_STATUSES = {
    TaskStatus.SUCCEEDED.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELED.value,
    TaskStatus.TIMED_OUT.value,
}
MAX_TASK_LIST_LIMIT = 100
MAX_IDEMPOTENCY_KEY_LENGTH = 128


def _task_response(task: Task) -> TaskResponse:
    """Convert a task ORM object into the API response model."""
    return TaskResponse(
        task_id=task.id,
        org_id=task.org_id,
        requested_by_user_id=task.requested_by_user_id,
        status=TaskStatus(task.status),
        prompt=task.prompt,
        error_message=task.error_message,
        created_at=task.created_at,
        updated_at=task.updated_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
    )


def _idempotency_replay_response(task: Task) -> TaskResponse | JSONResponse:
    """Build replay response for an existing idempotency-key task."""
    if task.status == TaskStatus.FAILED.value:
        return build_error_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="TASK_PREVIOUS_ATTEMPT_FAILED",
            message=(
                "A previous request with this Idempotency-Key failed. "
                "Use a new Idempotency-Key to retry."
            ),
        )
    if task.status in {TaskStatus.CANCELED.value, TaskStatus.TIMED_OUT.value}:
        return build_error_response(
            status_code=status.HTTP_409_CONFLICT,
            code="TASK_IDEMPOTENCY_KEY_TERMINAL",
            message=(
                f"A previous request with this Idempotency-Key reached terminal status "
                f"'{task.status}'"
            ),
        )
    return _task_response(task)


@router.get("/tasks", response_model=TaskListResponse)
def list_tasks(
    db: DBSession,
    principal: Annotated[TaskApiPrincipal, Depends(get_task_api_principal)],
    limit: Annotated[int, Query(ge=1, le=MAX_TASK_LIST_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    task_status: Annotated[TaskStatus | None, Query(alias="status")] = None,
) -> TaskListResponse:
    """List current tasks from persistent storage."""
    filters = [Task.org_id == principal.org_id]
    if task_status is not None:
        filters.append(Task.status == task_status.value)
    statement = (
        select(Task).where(*filters).order_by(Task.created_at.desc()).limit(limit).offset(offset)
    )
    tasks = db.execute(statement).scalars().all()
    total_count = db.execute(select(func.count()).select_from(Task).where(*filters)).scalar_one()
    items = [_task_response(task) for task in tasks]
    return TaskListResponse(items=items, count=int(total_count))


@router.post(
    "/tasks",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TaskResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def create_task(
    payload: TaskCreateRequest,
    db: DBSession,
    bus: EventBusDep,
    principal: Annotated[TaskApiPrincipal, Depends(get_task_api_principal)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TaskResponse | JSONResponse:
    """Create and persist a queued task."""
    normalized_idempotency_key = None
    if idempotency_key is not None:
        normalized_idempotency_key = idempotency_key.strip()
        if not normalized_idempotency_key:
            return build_error_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="TASK_CREATE_INVALID_IDEMPOTENCY_KEY",
                message="Idempotency-Key cannot be blank",
            )
        if len(normalized_idempotency_key) > MAX_IDEMPOTENCY_KEY_LENGTH:
            return build_error_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="TASK_CREATE_INVALID_IDEMPOTENCY_KEY",
                message=f"Idempotency-Key cannot exceed {MAX_IDEMPOTENCY_KEY_LENGTH} characters",
            )
        existing_task = db.execute(
            select(Task).where(
                Task.org_id == principal.org_id,
                Task.requested_by_user_id == principal.user_id,
                Task.idempotency_key == normalized_idempotency_key,
            )
        ).scalar_one_or_none()
        if existing_task is not None:
            return _idempotency_replay_response(existing_task)

    now = datetime.now(UTC)
    task = Task(
        org_id=principal.org_id,
        requested_by_user_id=principal.user_id,
        status=TaskStatus.QUEUED.value,
        prompt=payload.prompt,
        idempotency_key=normalized_idempotency_key,
        created_at=now,
        updated_at=now,
    )
    db.add(task)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if normalized_idempotency_key is not None:
            existing_task = db.execute(
                select(Task).where(
                    Task.org_id == principal.org_id,
                    Task.requested_by_user_id == principal.user_id,
                    Task.idempotency_key == normalized_idempotency_key,
                )
            ).scalar_one_or_none()
            if existing_task is not None:
                return _idempotency_replay_response(existing_task)
        return build_error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="TASK_CREATE_INVALID_REFERENCE",
            message="Task could not be persisted",
        )
    db.refresh(task)
    log_event(
        logger,
        event="task.lifecycle.created",
        task_id=task.id,
        org_id=task.org_id,
        requested_by_user_id=task.requested_by_user_id,
        status=task.status,
    )
    try:
        accepted = bus.enqueue(
            TASK_QUEUE,
            task.id,
            {
                "task_id": task.id,
                "org_id": task.org_id,
                "requested_by_user_id": task.requested_by_user_id,
                "status": task.status,
            },
        )
    except QUEUE_EXCEPTIONS:
        accepted = False
        logger.exception("Failed to enqueue task %s", task.id)

    if not accepted:
        failure_time = datetime.now(UTC)
        task.status = TaskStatus.FAILED.value
        task.error_message = "Queue backend unavailable during enqueue"
        task.completed_at = failure_time
        task.updated_at = failure_time
        db.add(task)
        db.commit()
        log_event(
            logger,
            event="task.lifecycle.enqueue_failed",
            task_id=task.id,
            queue=TASK_QUEUE,
            final_status=task.status,
        )
        return build_error_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="TASK_QUEUE_UNAVAILABLE",
            message="Task enqueue failed because the queue backend is unavailable",
        )
    log_event(
        logger,
        event="task.lifecycle.enqueued",
        task_id=task.id,
        queue=TASK_QUEUE,
        status=task.status,
    )
    return _task_response(task)


@router.get(
    "/tasks/{task_id}",
    response_model=TaskResponse,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
def get_task(
    task_id: UUID,
    db: DBSession,
    principal: Annotated[TaskApiPrincipal, Depends(get_task_api_principal)],
) -> TaskResponse | JSONResponse:
    """Fetch a task by id."""
    task_id_str = str(task_id)
    task = db.execute(
        select(Task).where(
            Task.id == task_id_str,
            Task.org_id == principal.org_id,
        )
    ).scalar_one_or_none()
    if task is None:
        return build_error_response(
            status_code=status.HTTP_404_NOT_FOUND,
            code="TASK_NOT_FOUND",
            message=f"Task '{task_id_str}' was not found",
        )
    return _task_response(task)


@router.post(
    "/tasks/{task_id}/cancel",
    response_model=TaskResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
def cancel_task(
    task_id: UUID,
    db: DBSession,
    principal: Annotated[TaskApiPrincipal, Depends(get_task_api_principal)],
) -> TaskResponse | JSONResponse:
    """Cancel a task when it is not terminal."""
    task_id_str = str(task_id)
    task = db.execute(
        select(Task).where(
            Task.id == task_id_str,
            Task.org_id == principal.org_id,
        )
    ).scalar_one_or_none()
    if task is None:
        return build_error_response(
            status_code=status.HTTP_404_NOT_FOUND,
            code="TASK_NOT_FOUND",
            message=f"Task '{task_id_str}' was not found",
        )

    if task.status in TERMINAL_STATUSES:
        if task.status == TaskStatus.CANCELED.value:
            return _task_response(task)
        return build_error_response(
            status_code=status.HTTP_409_CONFLICT,
            code="TASK_NOT_CANCELABLE",
            message=f"Task '{task_id_str}' is already terminal ({task.status})",
        )

    now = datetime.now(UTC)
    task.status = TaskStatus.CANCELED.value
    task.completed_at = now
    task.updated_at = now
    db.add(task)
    db.commit()
    db.refresh(task)
    log_event(
        logger,
        event="task.lifecycle.canceled",
        task_id=task.id,
        status=task.status,
    )
    return _task_response(task)
