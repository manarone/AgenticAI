from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, status

router = APIRouter(prefix="/v1", tags=["v1"])


@router.get("/tasks")
def list_tasks() -> dict[str, object]:
    return {"items": [], "count": 0}


@router.post("/tasks", status_code=status.HTTP_202_ACCEPTED)
def create_task() -> dict[str, str]:
    # Temporary stub to keep contract shape stable while task orchestration is built.
    return {
        "task_id": str(uuid4()),
        "status": "QUEUED",
        "created_at": datetime.now(UTC).isoformat(),
    }
