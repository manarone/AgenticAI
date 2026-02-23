"""Task API request and response contracts."""

from datetime import datetime

from pydantic import BaseModel, Field

from agenticai.db.models import ApprovalDecision, BypassMode, RiskTier, TaskStatus


class TaskCreateRequest(BaseModel):
    """Payload accepted by task creation endpoint."""

    prompt: str = Field(min_length=1, max_length=8192)


class TaskResponse(BaseModel):
    """Canonical task payload returned by task endpoints."""

    task_id: str
    org_id: str
    requested_by_user_id: str
    status: TaskStatus
    risk_tier: RiskTier | None
    approval_required: bool
    approval_decision: ApprovalDecision | None
    prompt: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class TaskListResponse(BaseModel):
    """Response payload for task listing."""

    items: list[TaskResponse]
    count: int  # Total matching tasks for the applied filters.


class ApprovalDecisionRequest(BaseModel):
    """Payload accepted by approval decision endpoint."""

    decision: ApprovalDecision
    reason: str | None = Field(default=None, max_length=2048)


class ApprovalResponse(BaseModel):
    """Approval record returned by approval APIs."""

    approval_id: str
    task_id: str
    org_id: str
    risk_tier: RiskTier
    decision: ApprovalDecision
    reason: str | None
    requested_by_user_id: str | None
    decided_by_user_id: str | None
    created_at: datetime
    updated_at: datetime
    decided_at: datetime | None
    task_status: TaskStatus


class ApprovalListResponse(BaseModel):
    """Response payload for approval list queries."""

    items: list[ApprovalResponse]
    count: int


class BypassModeUpdateRequest(BaseModel):
    """Payload accepted by bypass mode update endpoint."""

    bypass_mode: BypassMode
    reason: str | None = Field(default=None, max_length=2048)
    expires_at: datetime | None = None


class BypassModeResponse(BaseModel):
    """User bypass mode state after org policy enforcement."""

    user_id: str
    org_id: str
    bypass_mode: BypassMode
    effective_bypass_mode: BypassMode
    org_bypass_allowed: bool
    reason: str | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AuditEventResponse(BaseModel):
    """Audit event payload returned by query endpoint."""

    audit_event_id: str
    org_id: str
    task_id: str | None
    actor_user_id: str | None
    event_type: str
    event_payload: dict[str, object] | None
    created_at: datetime


class AuditEventListResponse(BaseModel):
    """Paginated response payload for audit event queries."""

    items: list[AuditEventResponse]
    count: int


class ErrorDetail(BaseModel):
    """Structured API error content."""

    code: str
    message: str


class ErrorResponse(BaseModel):
    """Common API error envelope."""

    error: ErrorDetail
