"""Phase 2 PR3: Track B schema foundation for approvals and auditability."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260223_0006"
down_revision: str | None = "20260222_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RISK_TIER_EXPR = "('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')"
_APPROVAL_DECISION_EXPR = "('PENDING', 'APPROVED', 'DENIED')"
_BYPASS_MODE_EXPR = "('DISABLED', 'LOW_RISK_ONLY', 'ALL_RISK')"


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(sa.Column("risk_tier", sa.String(length=16), nullable=True))
        batch_op.add_column(
            sa.Column(
                "approval_required",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(sa.Column("approval_decision", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("approval_requested_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("approval_decided_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("approved_by_user_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("execution_backend", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("execution_metadata", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "execution_attempts",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.add_column(sa.Column("execution_last_heartbeat_at", sa.DateTime(timezone=True)))
        batch_op.create_check_constraint(
            "ck_tasks_risk_tier",
            f"risk_tier IS NULL OR risk_tier IN {_RISK_TIER_EXPR}",
        )
        batch_op.create_check_constraint(
            "ck_tasks_approval_decision",
            f"approval_decision IS NULL OR approval_decision IN {_APPROVAL_DECISION_EXPR}",
        )
        batch_op.create_foreign_key(
            "fk_tasks_approved_by_user_id_users",
            "users",
            ["approved_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_tasks_approved_by_user_id", ["approved_by_user_id"], unique=False)
        batch_op.create_index("ix_tasks_status_updated_at", ["status", "updated_at"], unique=False)
        batch_op.create_index(
            "ix_tasks_org_approval_required_status",
            ["org_id", "approval_required", "status"],
            unique=False,
        )

    op.create_table(
        "approvals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("requested_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("decided_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("risk_tier", sa.String(length=16), nullable=False),
        sa.Column(
            "decision",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"risk_tier IN {_RISK_TIER_EXPR}", name="ck_approvals_risk_tier"),
        sa.CheckConstraint(
            f"decision IN {_APPROVAL_DECISION_EXPR}",
            name="ck_approvals_decision",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_approvals_org_id", "approvals", ["org_id"], unique=False)
    op.create_index("ix_approvals_task_id", "approvals", ["task_id"], unique=False)
    op.create_index(
        "ix_approvals_requested_by_user_id",
        "approvals",
        ["requested_by_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_approvals_decided_by_user_id",
        "approvals",
        ["decided_by_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_approvals_org_decision_created_at",
        "approvals",
        ["org_id", "decision", "created_at"],
        unique=False,
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("actor_user_id", sa.String(length=36), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("event_payload", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_org_id", "audit_events", ["org_id"], unique=False)
    op.create_index("ix_audit_events_task_id", "audit_events", ["task_id"], unique=False)
    op.create_index(
        "ix_audit_events_actor_user_id",
        "audit_events",
        ["actor_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_audit_events_org_created_at",
        "audit_events",
        ["org_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_audit_events_task_created_at",
        "audit_events",
        ["task_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "user_policy_overrides",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column(
            "bypass_mode",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'DISABLED'"),
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"bypass_mode IN {_BYPASS_MODE_EXPR}",
            name="ck_user_policy_overrides_bypass_mode",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "user_id", name="uq_user_policy_overrides_org_user"),
    )
    op.create_index(
        "ix_user_policy_overrides_org_id",
        "user_policy_overrides",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        "ix_user_policy_overrides_user_id",
        "user_policy_overrides",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_user_policy_overrides_org_bypass_mode",
        "user_policy_overrides",
        ["org_id", "bypass_mode"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_user_policy_overrides_org_bypass_mode",
        table_name="user_policy_overrides",
    )
    op.drop_index("ix_user_policy_overrides_user_id", table_name="user_policy_overrides")
    op.drop_index("ix_user_policy_overrides_org_id", table_name="user_policy_overrides")
    op.drop_table("user_policy_overrides")

    op.drop_index("ix_audit_events_task_created_at", table_name="audit_events")
    op.drop_index("ix_audit_events_org_created_at", table_name="audit_events")
    op.drop_index("ix_audit_events_actor_user_id", table_name="audit_events")
    op.drop_index("ix_audit_events_task_id", table_name="audit_events")
    op.drop_index("ix_audit_events_org_id", table_name="audit_events")
    op.drop_table("audit_events")

    op.drop_index("ix_approvals_org_decision_created_at", table_name="approvals")
    op.drop_index("ix_approvals_decided_by_user_id", table_name="approvals")
    op.drop_index("ix_approvals_requested_by_user_id", table_name="approvals")
    op.drop_index("ix_approvals_task_id", table_name="approvals")
    op.drop_index("ix_approvals_org_id", table_name="approvals")
    op.drop_table("approvals")

    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_index("ix_tasks_org_approval_required_status")
        batch_op.drop_index("ix_tasks_status_updated_at")
        batch_op.drop_index("ix_tasks_approved_by_user_id")
        batch_op.drop_constraint("fk_tasks_approved_by_user_id_users", type_="foreignkey")
        batch_op.drop_constraint("ck_tasks_approval_decision", type_="check")
        batch_op.drop_constraint("ck_tasks_risk_tier", type_="check")
        batch_op.drop_column("execution_last_heartbeat_at")
        batch_op.drop_column("execution_attempts")
        batch_op.drop_column("execution_metadata")
        batch_op.drop_column("execution_backend")
        batch_op.drop_column("approved_by_user_id")
        batch_op.drop_column("approval_decided_at")
        batch_op.drop_column("approval_requested_at")
        batch_op.drop_column("approval_decision")
        batch_op.drop_column("approval_required")
        batch_op.drop_column("risk_tier")
