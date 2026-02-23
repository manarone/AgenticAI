"""Shared FastAPI dependencies."""

from collections.abc import Generator
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from jwt import ExpiredSignatureError, InvalidAudienceError, InvalidTokenError
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


def _task_api_unauthorized(message: str) -> HTTPException:
    """Build a stable unauthorized exception payload for task APIs."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "code": "TASK_API_UNAUTHORIZED",
            "message": message,
        },
    )


def get_task_api_principal(
    request: Request,
    db: Annotated[Session, Depends(get_db_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> TaskApiPrincipal:
    """Authenticate a JWT caller and resolve a tenant-safe user/org principal."""
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        settings = get_settings()

    jwt_secret = (
        settings.task_api_jwt_secret.get_secret_value() if settings.task_api_jwt_secret else None
    )
    if jwt_secret is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "TASK_API_AUTH_MISCONFIGURED",
                "message": "Task API JWT auth requires TASK_API_JWT_SECRET",
            },
        )

    presented_token = _parse_bearer_token(authorization)
    if presented_token is None:
        raise _task_api_unauthorized("Invalid or missing bearer token")

    try:
        claims = jwt.decode(
            presented_token,
            jwt_secret,
            algorithms=[settings.task_api_jwt_algorithm],
            audience=settings.task_api_jwt_audience,
            options={"require": ["sub", "org_id", "exp", "iat", "aud"]},
        )
    except ExpiredSignatureError as exc:
        raise _task_api_unauthorized("Task API JWT has expired") from exc
    except InvalidAudienceError as exc:
        raise _task_api_unauthorized("Task API JWT audience mismatch") from exc
    except InvalidTokenError as exc:
        raise _task_api_unauthorized("Invalid task API JWT") from exc

    raw_sub = claims.get("sub")
    raw_org_id = claims.get("org_id")
    if not isinstance(raw_sub, str) or not raw_sub.strip():
        raise _task_api_unauthorized("Task API JWT claim 'sub' is required")
    if not isinstance(raw_org_id, str) or not raw_org_id.strip():
        raise _task_api_unauthorized("Task API JWT claim 'org_id' is required")

    try:
        normalized_sub = str(UUID(raw_sub.strip()))
    except ValueError as exc:
        raise _task_api_unauthorized("Task API JWT claim 'sub' must be a valid UUID") from exc
    try:
        normalized_org_id = str(UUID(raw_org_id.strip()))
    except ValueError as exc:
        raise _task_api_unauthorized("Task API JWT claim 'org_id' must be a valid UUID") from exc

    user = db.get(User, normalized_sub)
    if user is None:
        raise _task_api_unauthorized("Unknown task API JWT subject")
    if user.org_id != normalized_org_id:
        raise _task_api_unauthorized("Task API JWT org/user mismatch")

    return TaskApiPrincipal(org_id=normalized_org_id, user_id=normalized_sub)
