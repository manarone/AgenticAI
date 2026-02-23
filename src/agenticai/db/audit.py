"""Audit event persistence helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from agenticai.core.request_context import get_request_id
from agenticai.db.models import AuditEvent


def add_audit_event(
    session: Session,
    *,
    org_id: str,
    event_type: str,
    task_id: str | None = None,
    actor_user_id: str | None = None,
    event_payload: dict[str, object] | None = None,
    created_at: datetime | None = None,
) -> AuditEvent:
    """Insert one audit event row into the active session."""
    payload = dict(event_payload or {})
    request_id = get_request_id()
    if request_id is not None and "request_id" not in payload:
        payload["request_id"] = request_id

    serialized_payload = None
    if payload:
        serialized_payload = json.dumps(payload, sort_keys=True)
    audit_event = AuditEvent(
        org_id=org_id,
        task_id=task_id,
        actor_user_id=actor_user_id,
        event_type=event_type,
        event_payload=serialized_payload,
        created_at=created_at or datetime.now(UTC),
    )
    session.add(audit_event)
    return audit_event
