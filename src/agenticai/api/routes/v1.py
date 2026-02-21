from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, status

router = APIRouter(prefix="/v1", tags=["v1"])


@router.get("/tasks")
def list_tasks() -> dict[str, object]:
    """Return a placeholder task list until persistence is wired."""
    return {"items": [], "count": 0}


@router.post("/tasks", status_code=status.HTTP_202_ACCEPTED)
def create_task() -> dict[str, str]:
    """Create a placeholder task record for contract validation."""
    return {
        "task_id": str(uuid4()),
        "status": "QUEUED",
        "created_at": datetime.now(UTC).isoformat(),
    }
