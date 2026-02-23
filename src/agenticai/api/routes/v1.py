import logging
from datetime import UTC, datetime
from json import JSONDecodeError, loads
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
    ApprovalDecisionRequest,
    ApprovalListResponse,
    ApprovalResponse,
    AuditEventListResponse,
    AuditEventResponse,
    BypassModeResponse,
    BypassModeUpdateRequest,
    ErrorResponse,
    TaskCreateRequest,
    TaskListResponse,
    TaskResponse,
)
from agenticai.bus.base import TASK_QUEUE, EventBus
from agenticai.bus.exceptions import QUEUE_EXCEPTIONS
from agenticai.core.observability import log_event
from agenticai.db.audit import add_audit_event
from agenticai.db.models import (
    Approval,
    ApprovalDecision,
    AuditEvent,
    BypassMode,
    Task,
    TaskStatus,
    User,
    UserPolicyOverride,
)
from agenticai.db.policy import (
    get_user_policy_override,
    org_allows_user_bypass,
    resolve_effective_bypass_mode,
)

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
        risk_tier=task.risk_tier,
        approval_required=task.approval_required,
        approval_decision=task.approval_decision,
        prompt=task.prompt,
        error_message=task.error_message,
        created_at=task.created_at,
        updated_at=task.updated_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
    )


def _approval_response(approval: Approval, *, task_status: str) -> ApprovalResponse:
    """Convert an approval ORM record into API response payload."""
    return ApprovalResponse(
        approval_id=approval.id,
        task_id=approval.task_id,
        org_id=approval.org_id,
        risk_tier=approval.risk_tier,
        decision=approval.decision,
        reason=approval.reason,
        requested_by_user_id=approval.requested_by_user_id,
        decided_by_user_id=approval.decided_by_user_id,
        created_at=approval.created_at,
        updated_at=approval.updated_at,
        decided_at=approval.decided_at,
        task_status=task_status,
    )


def _bypass_mode_response(
    override: UserPolicyOverride,
    *,
    org_bypass_allowed: bool,
    effective_bypass_mode: BypassMode,
) -> BypassModeResponse:
    return BypassModeResponse(
        user_id=override.user_id,
        org_id=override.org_id,
        bypass_mode=override.bypass_mode,
        effective_bypass_mode=effective_bypass_mode,
        org_bypass_allowed=org_bypass_allowed,
        reason=override.reason,
        expires_at=override.expires_at,
        created_at=override.created_at,
        updated_at=override.updated_at,
    )


def _audit_event_response(event: AuditEvent) -> AuditEventResponse:
    payload = None
    if event.event_payload is not None:
        try:
            decoded_payload = loads(event.event_payload)
            if isinstance(decoded_payload, dict):
                payload = decoded_payload
        except JSONDecodeError:
            payload = {"raw_payload": event.event_payload}
    return AuditEventResponse(
        audit_event_id=event.id,
        org_id=event.org_id,
        task_id=event.task_id,
        actor_user_id=event.actor_user_id,
        event_type=event.event_type,
        event_payload=payload,
        created_at=event.created_at,
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
    add_audit_event(
        db,
        org_id=task.org_id,
        task_id=task.id,
        actor_user_id=task.requested_by_user_id,
        event_type="task.lifecycle.created",
        event_payload={"status": task.status},
        created_at=now,
    )
    db.commit()
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
        add_audit_event(
            db,
            org_id=task.org_id,
            task_id=task.id,
            actor_user_id=task.requested_by_user_id,
            event_type="task.lifecycle.enqueue_failed",
            event_payload={"status": task.status, "queue": TASK_QUEUE},
            created_at=failure_time,
        )
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
    add_audit_event(
        db,
        org_id=task.org_id,
        task_id=task.id,
        actor_user_id=task.requested_by_user_id,
        event_type="task.lifecycle.enqueued",
        event_payload={"status": task.status, "queue": TASK_QUEUE},
    )
    db.commit()
    return _task_response(task)


@router.get("/approvals", response_model=ApprovalListResponse)
def list_approvals(
    db: DBSession,
    principal: Annotated[TaskApiPrincipal, Depends(get_task_api_principal)],
    limit: Annotated[int, Query(ge=1, le=MAX_TASK_LIST_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    decision: Annotated[ApprovalDecision | None, Query()] = None,
) -> ApprovalListResponse:
    """List approvals scoped to the authenticated principal organization."""
    filters = [Approval.org_id == principal.org_id]
    if decision is not None:
        filters.append(Approval.decision == decision.value)

    statement = (
        select(Approval, Task.status)
        .join(Task, Task.id == Approval.task_id)
        .where(*filters)
        .order_by(Approval.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = db.execute(statement).all()
    total_count = db.execute(
        select(func.count()).select_from(Approval).where(*filters)
    ).scalar_one()
    items = [_approval_response(row[0], task_status=row[1]) for row in rows]
    return ApprovalListResponse(items=items, count=int(total_count))


@router.post(
    "/approvals/{approval_id}/decision",
    response_model=ApprovalResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def decide_approval(
    approval_id: UUID,
    payload: ApprovalDecisionRequest,
    db: DBSession,
    bus: EventBusDep,
    principal: Annotated[TaskApiPrincipal, Depends(get_task_api_principal)],
) -> ApprovalResponse | JSONResponse:
    """Apply an approval decision and resume or fail task lifecycle."""
    approval_id_str = str(approval_id)
    row = db.execute(
        select(Approval, Task)
        .join(Task, Task.id == Approval.task_id)
        .where(
            Approval.id == approval_id_str,
            Approval.org_id == principal.org_id,
            Task.org_id == principal.org_id,
        )
    ).one_or_none()
    if row is None:
        return build_error_response(
            status_code=status.HTTP_404_NOT_FOUND,
            code="APPROVAL_NOT_FOUND",
            message=f"Approval '{approval_id_str}' was not found",
        )
    approval, task = row

    if approval.decision != ApprovalDecision.PENDING.value:
        return build_error_response(
            status_code=status.HTTP_409_CONFLICT,
            code="APPROVAL_ALREADY_DECIDED",
            message=f"Approval '{approval_id_str}' is already {approval.decision}",
        )
    if task.status != TaskStatus.WAITING_APPROVAL.value:
        return build_error_response(
            status_code=status.HTTP_409_CONFLICT,
            code="TASK_NOT_WAITING_APPROVAL",
            message=f"Task '{task.id}' is not waiting for approval",
        )

    decision_time = datetime.now(UTC)
    normalized_reason = None
    if payload.reason is not None:
        normalized_reason = payload.reason.strip() or None
    approval.decision = payload.decision.value
    approval.reason = normalized_reason
    approval.decided_by_user_id = principal.user_id
    approval.decided_at = decision_time
    approval.updated_at = decision_time
    task.approval_decision = payload.decision.value
    task.approval_decided_at = decision_time
    task.updated_at = decision_time

    if payload.decision == ApprovalDecision.DENIED:
        task.status = TaskStatus.FAILED.value
        task.error_message = normalized_reason or "Task denied by approver"
        task.completed_at = decision_time
        task.approved_by_user_id = None
        db.add(approval)
        db.add(task)
        add_audit_event(
            db,
            org_id=task.org_id,
            task_id=task.id,
            actor_user_id=principal.user_id,
            event_type="approval.decision.denied",
            event_payload={"approval_id": approval.id, "reason": normalized_reason},
            created_at=decision_time,
        )
        add_audit_event(
            db,
            org_id=task.org_id,
            task_id=task.id,
            actor_user_id=principal.user_id,
            event_type="task.lifecycle.failed",
            event_payload={"status": task.status, "reason": task.error_message},
            created_at=decision_time,
        )
        db.commit()
        db.refresh(approval)
        log_event(
            logger,
            event="approval.decision.denied",
            approval_id=approval.id,
            task_id=task.id,
            org_id=task.org_id,
            decided_by_user_id=principal.user_id,
        )
        return _approval_response(approval, task_status=task.status)

    task.approved_by_user_id = principal.user_id
    task.error_message = None
    task.completed_at = None
    db.add(approval)
    db.add(task)
    db.flush()

    try:
        accepted = bus.enqueue(
            TASK_QUEUE,
            task.id,
            {
                "task_id": task.id,
                "org_id": task.org_id,
                "requested_by_user_id": task.requested_by_user_id,
                "status": task.status,
                "approval_id": approval.id,
                "approval_decision": approval.decision,
            },
        )
    except QUEUE_EXCEPTIONS:
        accepted = False
        logger.exception("Failed to enqueue approved task %s", task.id)
    if not accepted:
        db.rollback()
        return build_error_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="TASK_QUEUE_UNAVAILABLE",
            message="Task enqueue failed because the queue backend is unavailable",
        )

    add_audit_event(
        db,
        org_id=task.org_id,
        task_id=task.id,
        actor_user_id=principal.user_id,
        event_type="approval.decision.approved",
        event_payload={"approval_id": approval.id, "reason": normalized_reason},
        created_at=decision_time,
    )
    db.commit()
    db.refresh(approval)
    log_event(
        logger,
        event="approval.decision.approved",
        approval_id=approval.id,
        task_id=task.id,
        org_id=task.org_id,
        decided_by_user_id=principal.user_id,
    )
    return _approval_response(approval, task_status=task.status)


@router.post(
    "/users/{user_id}/bypass-mode",
    response_model=BypassModeResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
def update_user_bypass_mode(
    user_id: UUID,
    payload: BypassModeUpdateRequest,
    db: DBSession,
    principal: Annotated[TaskApiPrincipal, Depends(get_task_api_principal)],
) -> BypassModeResponse | JSONResponse:
    """Set bypass mode for the authenticated user with org policy enforcement."""
    user_id_str = str(user_id)
    if user_id_str != principal.user_id:
        return build_error_response(
            status_code=status.HTTP_403_FORBIDDEN,
            code="BYPASS_MODE_FORBIDDEN",
            message="You may only update your own bypass mode",
        )

    user = db.get(User, user_id_str)
    if user is None or user.org_id != principal.org_id:
        return build_error_response(
            status_code=status.HTTP_404_NOT_FOUND,
            code="USER_NOT_FOUND",
            message=f"User '{user_id_str}' was not found",
        )

    org_bypass_allowed = org_allows_user_bypass(db, principal.org_id)
    now = datetime.now(UTC)
    requested_mode = payload.bypass_mode
    if requested_mode != BypassMode.DISABLED and not org_bypass_allowed:
        add_audit_event(
            db,
            org_id=principal.org_id,
            actor_user_id=principal.user_id,
            event_type="policy.bypass.blocked_by_org",
            event_payload={"requested_mode": requested_mode.value},
            created_at=now,
        )
        db.commit()
        return build_error_response(
            status_code=status.HTTP_409_CONFLICT,
            code="ORG_POLICY_BYPASS_DISABLED",
            message="Organization policy currently disallows user bypass overrides",
        )

    override = get_user_policy_override(
        db,
        org_id=principal.org_id,
        user_id=principal.user_id,
    )
    if override is None:
        override = UserPolicyOverride(
            org_id=principal.org_id,
            user_id=principal.user_id,
            bypass_mode=BypassMode.DISABLED.value,
            created_at=now,
            updated_at=now,
        )
    normalized_reason = None
    if payload.reason is not None:
        normalized_reason = payload.reason.strip() or None
    override.bypass_mode = requested_mode.value
    override.reason = normalized_reason
    override.expires_at = payload.expires_at
    override.updated_at = now
    db.add(override)
    db.flush()

    effective_mode = resolve_effective_bypass_mode(
        db,
        org_id=principal.org_id,
        user_id=principal.user_id,
    )
    add_audit_event(
        db,
        org_id=principal.org_id,
        actor_user_id=principal.user_id,
        event_type="policy.bypass.updated",
        event_payload={
            "requested_mode": requested_mode.value,
            "effective_mode": effective_mode.value,
            "org_bypass_allowed": org_bypass_allowed,
        },
        created_at=now,
    )
    db.commit()
    db.refresh(override)
    return _bypass_mode_response(
        override,
        org_bypass_allowed=org_bypass_allowed,
        effective_bypass_mode=effective_mode,
    )


@router.get("/audit-events", response_model=AuditEventListResponse)
def list_audit_events(
    db: DBSession,
    principal: Annotated[TaskApiPrincipal, Depends(get_task_api_principal)],
    limit: Annotated[int, Query(ge=1, le=MAX_TASK_LIST_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    task_id: Annotated[UUID | None, Query()] = None,
    actor_user_id: Annotated[UUID | None, Query()] = None,
    event_type: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
) -> AuditEventListResponse:
    """List tenant-scoped audit events for policy and lifecycle operations."""
    filters = [AuditEvent.org_id == principal.org_id]
    if task_id is not None:
        filters.append(AuditEvent.task_id == str(task_id))
    if actor_user_id is not None:
        filters.append(AuditEvent.actor_user_id == str(actor_user_id))
    if event_type is not None:
        filters.append(AuditEvent.event_type == event_type)

    statement = (
        select(AuditEvent)
        .where(*filters)
        .order_by(AuditEvent.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    items = db.execute(statement).scalars().all()
    total_count = db.execute(
        select(func.count()).select_from(AuditEvent).where(*filters)
    ).scalar_one()
    return AuditEventListResponse(
        items=[_audit_event_response(event) for event in items],
        count=int(total_count),
    )


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
    add_audit_event(
        db,
        org_id=task.org_id,
        task_id=task.id,
        actor_user_id=principal.user_id,
        event_type="task.lifecycle.canceled",
        event_payload={"status": task.status},
        created_at=now,
    )
    db.commit()
    db.refresh(task)
    log_event(
        logger,
        event="task.lifecycle.canceled",
        task_id=task.id,
        status=task.status,
    )
    return _task_response(task)
