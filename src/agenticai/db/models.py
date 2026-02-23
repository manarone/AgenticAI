"""Core relational models for the foundation track."""

from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
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


class RiskTier(StrEnum):
    """Risk classification used for approval and policy decisions."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ApprovalDecision(StrEnum):
    """Approval outcomes applied to risky tasks."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"


class BypassMode(StrEnum):
    """User-level policy override mode for approval bypassing."""

    DISABLED = "DISABLED"
    LOW_RISK_ONLY = "LOW_RISK_ONLY"
    ALL_RISK = "ALL_RISK"


class TelegramWebhookOutcome(StrEnum):
    """Terminal outcomes for one Telegram webhook update."""

    TASK_ENQUEUED = "TASK_ENQUEUED"
    ENQUEUE_FAILED = "ENQUEUE_FAILED"
    REGISTERED = "REGISTERED"
    REGISTRATION_REQUIRED = "REGISTRATION_REQUIRED"
    IGNORED = "IGNORED"


class RuntimeSetting(Base):
    """Mutable runtime configuration seeded via migrations."""

    __tablename__ = "runtime_settings"

    key: Mapped[str] = mapped_column(String(length=128), primary_key=True)
    value: Mapped[str] = mapped_column(String(length=256), nullable=False)
    description: Mapped[str | None] = mapped_column(String(length=255), nullable=True)
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
    approvals: Mapped[list["Approval"]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    audit_events: Mapped[list["AuditEvent"]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    policy_overrides: Mapped[list["UserPolicyOverride"]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
    )


class User(Base):
    """Mapped user identity scoped to one organization."""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("telegram_user_id", name="uq_users_telegram_user_id"),)

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
    requested_tasks: Mapped[list["Task"]] = relationship(
        back_populates="requested_by_user",
        foreign_keys="Task.requested_by_user_id",
    )
    approval_requests: Mapped[list["Approval"]] = relationship(
        back_populates="requested_by_user",
        foreign_keys="Approval.requested_by_user_id",
    )
    approval_decisions: Mapped[list["Approval"]] = relationship(
        back_populates="decided_by_user",
        foreign_keys="Approval.decided_by_user_id",
    )
    approved_tasks: Mapped[list["Task"]] = relationship(
        back_populates="approved_by_user",
        foreign_keys="Task.approved_by_user_id",
    )
    policy_override: Mapped["UserPolicyOverride | None"] = relationship(
        back_populates="user",
        uselist=False,
    )
    audit_events: Mapped[list["AuditEvent"]] = relationship(back_populates="actor_user")


class Task(Base):
    """Persisted task request with lifecycle fields."""

    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_org_status", "org_id", "status"),
        Index("ix_tasks_created_at", "created_at"),
        Index("ix_tasks_status_updated_at", "status", "updated_at"),
        Index(
            "ix_tasks_org_approval_required_status",
            "org_id",
            "approval_required",
            "status",
        ),
        CheckConstraint(
            "risk_tier IS NULL OR risk_tier IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="ck_tasks_risk_tier",
        ),
        CheckConstraint(
            "approval_decision IS NULL OR approval_decision IN ('PENDING', 'APPROVED', 'DENIED')",
            name="ck_tasks_approval_decision",
        ),
        UniqueConstraint(
            "org_id",
            "requested_by_user_id",
            "idempotency_key",
            name="uq_tasks_org_user_idempotency_key",
        ),
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
    idempotency_key: Mapped[str | None] = mapped_column(String(length=128), nullable=True)
    risk_tier: Mapped[str | None] = mapped_column(
        String(length=16),
        nullable=True,
    )
    approval_required: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
    )
    approval_decision: Mapped[str | None] = mapped_column(
        String(length=16),
        nullable=True,
    )
    approval_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by_user_id: Mapped[str | None] = mapped_column(
        String(length=36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    execution_backend: Mapped[str | None] = mapped_column(String(length=32), nullable=True)
    execution_metadata: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    execution_last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    requested_by_user: Mapped["User"] = relationship(
        back_populates="requested_tasks",
        foreign_keys=[requested_by_user_id],
    )
    approved_by_user: Mapped["User | None"] = relationship(
        back_populates="approved_tasks",
        foreign_keys=[approved_by_user_id],
    )
    approvals: Mapped[list["Approval"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    audit_events: Mapped[list["AuditEvent"]] = relationship(
        back_populates="task",
        cascade="save-update, merge",
        passive_deletes=True,
    )
    webhook_events: Mapped[list["TelegramWebhookEvent"]] = relationship(
        back_populates="task",
        cascade="save-update, merge",
        passive_deletes=True,
    )


class Approval(Base):
    """Approval request/decision records associated with tasks."""

    __tablename__ = "approvals"
    __table_args__ = (
        Index("ix_approvals_org_id", "org_id"),
        Index("ix_approvals_task_id", "task_id"),
        Index("ix_approvals_requested_by_user_id", "requested_by_user_id"),
        Index("ix_approvals_decided_by_user_id", "decided_by_user_id"),
        Index("ix_approvals_org_decision_created_at", "org_id", "decision", "created_at"),
        CheckConstraint(
            "risk_tier IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="ck_approvals_risk_tier",
        ),
        CheckConstraint(
            "decision IN ('PENDING', 'APPROVED', 'DENIED')",
            name="ck_approvals_decision",
        ),
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
    )
    task_id: Mapped[str] = mapped_column(
        String(length=36),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    requested_by_user_id: Mapped[str | None] = mapped_column(
        String(length=36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    decided_by_user_id: Mapped[str | None] = mapped_column(
        String(length=36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    risk_tier: Mapped[str] = mapped_column(String(length=16), nullable=False)
    decision: Mapped[str] = mapped_column(
        String(length=16),
        nullable=False,
        default=ApprovalDecision.PENDING.value,
        server_default=ApprovalDecision.PENDING.value,
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organization: Mapped["Organization"] = relationship(back_populates="approvals")
    task: Mapped["Task"] = relationship(back_populates="approvals")
    requested_by_user: Mapped["User | None"] = relationship(
        back_populates="approval_requests",
        foreign_keys=[requested_by_user_id],
    )
    decided_by_user: Mapped["User | None"] = relationship(
        back_populates="approval_decisions",
        foreign_keys=[decided_by_user_id],
    )


class AuditEvent(Base):
    """Tenant-scoped audit log entries for security and compliance workflows."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_org_id", "org_id"),
        Index("ix_audit_events_task_id", "task_id"),
        Index("ix_audit_events_actor_user_id", "actor_user_id"),
        Index("ix_audit_events_org_created_at", "org_id", "created_at"),
        Index("ix_audit_events_task_created_at", "task_id", "created_at"),
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
    )
    task_id: Mapped[str | None] = mapped_column(
        String(length=36),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_user_id: Mapped[str | None] = mapped_column(
        String(length=36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(length=64), nullable=False)
    event_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    organization: Mapped["Organization"] = relationship(back_populates="audit_events")
    task: Mapped["Task | None"] = relationship(back_populates="audit_events")
    actor_user: Mapped["User | None"] = relationship(back_populates="audit_events")


class UserPolicyOverride(Base):
    """User-level policy override settings for approval bypass behavior."""

    __tablename__ = "user_policy_overrides"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_user_policy_overrides_org_user"),
        Index("ix_user_policy_overrides_org_id", "org_id"),
        Index("ix_user_policy_overrides_user_id", "user_id"),
        Index("ix_user_policy_overrides_org_bypass_mode", "org_id", "bypass_mode"),
        CheckConstraint(
            "bypass_mode IN ('DISABLED', 'LOW_RISK_ONLY', 'ALL_RISK')",
            name="ck_user_policy_overrides_bypass_mode",
        ),
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
    )
    user_id: Mapped[str] = mapped_column(
        String(length=36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    bypass_mode: Mapped[str] = mapped_column(
        String(length=32),
        nullable=False,
        default=BypassMode.DISABLED.value,
        server_default=BypassMode.DISABLED.value,
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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

    organization: Mapped["Organization"] = relationship(back_populates="policy_overrides")
    user: Mapped["User"] = relationship(back_populates="policy_override")


class TelegramWebhookEvent(Base):
    """Persisted Telegram webhook update for idempotent processing."""

    __tablename__ = "telegram_webhook_events"
    __table_args__ = (
        UniqueConstraint("update_id", name="uq_telegram_webhook_events_update_id"),
        Index("ix_telegram_webhook_events_telegram_user_id", "telegram_user_id"),
        CheckConstraint(
            (
                "outcome IN ("
                "'TASK_ENQUEUED', "
                "'ENQUEUE_FAILED', "
                "'REGISTERED', "
                "'REGISTRATION_REQUIRED', "
                "'IGNORED'"
                ")"
            ),
            name="ck_telegram_webhook_events_outcome",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(length=36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    update_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str] = mapped_column(
        String(length=32),
        nullable=False,
    )
    task_id: Mapped[str | None] = mapped_column(
        String(length=36),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    task: Mapped["Task | None"] = relationship(back_populates="webhook_events")
