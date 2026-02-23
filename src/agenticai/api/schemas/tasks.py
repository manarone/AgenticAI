"""Task API request and response contracts."""

from datetime import datetime

from pydantic import BaseModel, Field

from agenticai.db.models import ApprovalDecision, RiskTier, TaskStatus


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


class ErrorDetail(BaseModel):
    """Structured API error content."""

    code: str
    message: str


class ErrorResponse(BaseModel):
    """Common API error envelope."""

    error: ErrorDetail
