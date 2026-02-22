"""Shared FastAPI dependencies."""

from collections.abc import Generator
from dataclasses import dataclass
from typing import Annotated

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


def get_task_api_principal(
    request: Request,
    db: Annotated[Session, Depends(get_db_session)],
    authorization: Annotated[str | None, Header()] = None,
    actor_user_id: Annotated[str | None, Header(alias="X-Actor-User-Id")] = None,
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
        if presented_token != expected_token:
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

    user = db.get(User, actor_user_id.strip())
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "TASK_API_UNAUTHORIZED",
                "message": "Unknown actor user",
            },
        )
    return TaskApiPrincipal(org_id=user.org_id, user_id=user.id)
