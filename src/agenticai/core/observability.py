"""Shared observability helpers for structured lifecycle logging."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, time
from enum import Enum
from typing import Any

from agenticai.core.request_context import get_request_id


def _normalize_field_value(value: Any) -> Any:
    """Convert runtime values into JSON-safe primitives for logs."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _normalize_field_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_field_value(item) for item in value]
    return str(value)


def log_event(
    logger: logging.Logger,
    *,
    event: str,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """Emit a stable structured event record."""
    normalized_fields = {
        key: _normalize_field_value(value) for key, value in sorted(fields.items())
    }
    request_id = get_request_id()
    if request_id is not None and "request_id" not in normalized_fields:
        normalized_fields["request_id"] = request_id
    logger.log(
        level,
        "event=%s fields=%s",
        event,
        json.dumps(normalized_fields, sort_keys=True, separators=(",", ":")),
    )
