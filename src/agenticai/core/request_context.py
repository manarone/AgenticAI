"""Per-request context values used for cross-cutting correlation."""

from __future__ import annotations

from contextvars import ContextVar, Token

_REQUEST_ID: ContextVar[str | None] = ContextVar("agenticai_request_id", default=None)


def get_request_id() -> str | None:
    """Return the active request correlation identifier, if present."""
    return _REQUEST_ID.get()


def set_request_id(request_id: str) -> Token[str | None]:
    """Store a request identifier in context and return the reset token."""
    return _REQUEST_ID.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    """Restore previous request identifier state for current context."""
    _REQUEST_ID.reset(token)
