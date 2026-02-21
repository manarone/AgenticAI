"""Task API request and response contracts."""

from datetime import datetime

from pydantic import BaseModel, Field

from agenticai.db.models import TaskStatus


class TaskCreateRequest(BaseModel):
    """Payload accepted by task creation endpoint."""

    org_id: str = Field(min_length=1, max_length=36)
    requested_by_user_id: str = Field(min_length=1, max_length=36)
    prompt: str = Field(min_length=1)


class TaskResponse(BaseModel):
    """Canonical task payload returned by task endpoints."""

    task_id: str
    org_id: str
    requested_by_user_id: str
    status: TaskStatus
    prompt: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class TaskListResponse(BaseModel):
    """Response payload for task listing."""

    items: list[TaskResponse]
    count: int


class ErrorDetail(BaseModel):
    """Structured API error content."""

    code: str
    message: str


class ErrorResponse(BaseModel):
    """Common API error envelope."""

    error: ErrorDetail
