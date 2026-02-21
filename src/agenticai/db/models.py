"""Core relational models for the foundation track."""

from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agenticai.db.base import Base


class TaskStatus(StrEnum):
    """Lifecycle states for a task record."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    TIMED_OUT = "TIMED_OUT"


class Organization(Base):
    """Organization tenant for a dedicated deployment."""

    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(
        String(length=36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    slug: Mapped[str] = mapped_column(String(length=64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(length=128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    users: Mapped[list["User"]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    tasks: Mapped[list["Task"]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
    )


class User(Base):
    """Mapped user identity scoped to one organization."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("org_id", "telegram_user_id", name="uq_users_org_telegram_user_id"),
    )

    id: Mapped[str] = mapped_column(
        String(length=36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    org_id: Mapped[str] = mapped_column(
        String(length=36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(length=255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    organization: Mapped["Organization"] = relationship(back_populates="users")
    requested_tasks: Mapped[list["Task"]] = relationship(back_populates="requested_by_user")


class Task(Base):
    """Persisted task request with lifecycle fields."""

    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_org_status", "org_id", "status"),
        Index("ix_tasks_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(length=36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    org_id: Mapped[str] = mapped_column(
        String(length=36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    requested_by_user_id: Mapped[str] = mapped_column(
        String(length=36),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(length=32),
        nullable=False,
        default=TaskStatus.QUEUED.value,
    )
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organization: Mapped["Organization"] = relationship(back_populates="tasks")
    requested_by_user: Mapped["User"] = relationship(back_populates="requested_tasks")
