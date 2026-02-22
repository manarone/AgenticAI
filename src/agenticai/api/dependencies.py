"""Shared FastAPI dependencies."""

from collections.abc import Generator

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from agenticai.bus.base import EventBus


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
