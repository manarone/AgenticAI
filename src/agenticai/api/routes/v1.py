import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agenticai.api.dependencies import get_db_session, get_event_bus
from agenticai.api.responses import build_error_response
from agenticai.api.schemas.tasks import (
    ErrorResponse,
    TaskCreateRequest,
    TaskListResponse,
    TaskResponse,
)
from agenticai.bus.base import TASK_QUEUE, EventBus
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


@router.get("/tasks", response_model=TaskListResponse)
def list_tasks(db: DBSession) -> TaskListResponse:
    """List current tasks from persistent storage."""
    tasks = db.execute(select(Task).order_by(Task.created_at.desc())).scalars().all()
    items = [_task_response(task) for task in tasks]
    return TaskListResponse(items=items, count=len(items))


@router.post(
    "/tasks",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TaskResponse,
    responses={
        400: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def create_task(
    payload: TaskCreateRequest,
    db: DBSession,
    bus: EventBusDep,
) -> TaskResponse | JSONResponse:
    """Create and persist a queued task."""
    now = datetime.now(UTC)
    task = Task(
        org_id=payload.org_id,
        requested_by_user_id=payload.requested_by_user_id,
        status=TaskStatus.QUEUED.value,
        prompt=payload.prompt,
        created_at=now,
        updated_at=now,
    )
    db.add(task)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return build_error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="TASK_CREATE_INVALID_REFERENCE",
            message="org_id or requested_by_user_id does not exist",
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
    except Exception:
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
    responses={404: {"model": ErrorResponse}},
)
def get_task(
    task_id: str,
    db: DBSession,
) -> TaskResponse | JSONResponse:
    """Fetch a task by id."""
    task = db.get(Task, task_id)
    if task is None:
        return build_error_response(
            status_code=status.HTTP_404_NOT_FOUND,
            code="TASK_NOT_FOUND",
            message=f"Task '{task_id}' was not found",
        )
    return _task_response(task)


@router.post(
    "/tasks/{task_id}/cancel",
    response_model=TaskResponse,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
def cancel_task(
    task_id: str,
    db: DBSession,
) -> TaskResponse | JSONResponse:
    """Cancel a task when it is not terminal."""
    task = db.get(Task, task_id)
    if task is None:
        return build_error_response(
            status_code=status.HTTP_404_NOT_FOUND,
            code="TASK_NOT_FOUND",
            message=f"Task '{task_id}' was not found",
        )

    if task.status in TERMINAL_STATUSES:
        if task.status == TaskStatus.CANCELED.value:
            return _task_response(task)
        return build_error_response(
            status_code=status.HTTP_409_CONFLICT,
            code="TASK_NOT_CANCELABLE",
            message=f"Task '{task_id}' is already terminal ({task.status})",
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
