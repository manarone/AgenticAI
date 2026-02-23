"""Shared FastAPI dependencies."""

import hmac
from collections.abc import Generator
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session, sessionmaker

from agenticai.bus.base import EventBus
from agenticai.core.config import get_settings
from agenticai.db.models import User


def get_db_session(request: Request) -> Generator[Session, None, None]:
    """Yield a request-scoped SQLAlchemy session."""
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state,
        "db_session_factory",
        None,
    )
    if session_factory is None:
        raise RuntimeError("Database session factory is not initialized")

    session = session_factory()
    try:
        yield session
    finally:
        session.close()


def get_event_bus(request: Request) -> EventBus:
    """Return the initialized event bus from app state."""
    bus: EventBus | None = getattr(request.app.state, "bus", None)
    if bus is None:
        raise RuntimeError("Event bus is not initialized")
    return bus


@dataclass(frozen=True)
class TaskApiPrincipal:
    """Authenticated principal used for tenant-scoped task API access."""

    org_id: str
    user_id: str


def _parse_bearer_token(authorization: str | None) -> str | None:
    """Extract bearer token from Authorization header."""
    if authorization is None:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = value.strip()
    return token or None


def _normalized_actor_signature(signature: str | None) -> str | None:
    """Normalize supported actor signature formats to lowercase hex."""
    if signature is None:
        return None
    normalized = signature.strip()
    if normalized.lower().startswith("sha256="):
        normalized = normalized.split("=", 1)[1]
    return normalized.lower() or None


def get_task_api_principal(
    request: Request,
    db: Annotated[Session, Depends(get_db_session)],
    authorization: Annotated[str | None, Header()] = None,
    actor_user_id: Annotated[str | None, Header(alias="X-Actor-User-Id")] = None,
    actor_signature: Annotated[str | None, Header(alias="X-Actor-Signature")] = None,
) -> TaskApiPrincipal:
    """Authenticate a caller and resolve a user/org principal."""
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        settings = get_settings()

    expected_token = (
        settings.task_api_auth_token.get_secret_value()
        if settings.task_api_auth_token is not None
        else None
    )
    if expected_token is None:
        if not settings.allow_insecure_task_api:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "TASK_API_AUTH_MISCONFIGURED",
                    "message": (
                        "Task API authentication is required; set TASK_API_AUTH_TOKEN or "
                        "ALLOW_INSECURE_TASK_API=true"
                    ),
                },
            )
    else:
        presented_token = _parse_bearer_token(authorization)
        if presented_token is None or not hmac.compare_digest(presented_token, expected_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "TASK_API_UNAUTHORIZED",
                    "message": "Invalid or missing bearer token",
                },
            )

    if actor_user_id is None or not actor_user_id.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "TASK_API_UNAUTHORIZED",
                "message": "X-Actor-User-Id header is required",
            },
        )
    normalized_actor_user_id = actor_user_id.strip()
    try:
        UUID(normalized_actor_user_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "TASK_API_UNAUTHORIZED",
                "message": "X-Actor-User-Id must be a valid UUID",
            },
        ) from exc

    actor_hmac_secret = (
        settings.task_api_actor_hmac_secret.get_secret_value()
        if settings.task_api_actor_hmac_secret is not None
        else None
    )
    if actor_hmac_secret is not None:
        provided_signature = _normalized_actor_signature(actor_signature)
        expected_signature = hmac.new(
            actor_hmac_secret.encode("utf-8"),
            normalized_actor_user_id.encode("utf-8"),
            "sha256",
        ).hexdigest()
        if provided_signature is None or not hmac.compare_digest(
            provided_signature,
            expected_signature,
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "TASK_API_UNAUTHORIZED",
                    "message": "Invalid or missing X-Actor-Signature header",
                },
            )

    user = db.get(User, normalized_actor_user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "TASK_API_UNAUTHORIZED",
                "message": "Unknown actor user",
            },
        )
    return TaskApiPrincipal(org_id=user.org_id, user_id=user.id)
